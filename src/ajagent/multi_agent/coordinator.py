"""Deterministic routing and a single publisher for versioned artifacts."""
from dataclasses import asdict
import json
from ajagent.core.types import RunResult
from ajagent.harness.budget import BudgetExceeded
from ajagent.multi_agent.handoff import Handoff, citations, decode, validate


class NodeFailure(Exception):
    pass


class Coordinator:
    def __init__(self, parent, factory, options):
        self.parent, self.factory, self.options = parent, factory, options
        self.directory = parent.events.root / 'artifacts'
        self.directory.mkdir(exist_ok=True)
        self.evidence, self.paths, self.failures = {}, [], []
        self.last_usage = None
        self.node_count = 0

    def save(self, name, value):
        path = self.directory / name
        value = self.parent.clean(value)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        path.write_text(text, encoding='utf-8')
        self.paths.append(str(path))
        return path

    def node(self, role, goal, inputs, version):
        feedback = ''
        for attempt in range(self.options.node_retries + 1):
            budget = self.parent.budget
            budget.check()
            handoff = Handoff(self.parent.events.id, goal, dict(inputs, retry_feedback=feedback),
                              list(self.evidence), list(self.paths),
                              ['Support factual claims with observed evidence.', 'Report gaps and conflicts.',
                               'Follow the role JSON contract; never declare workflow success.'],
                              {'tokens': budget.max_tokens - budget.tokens - budget.reserved,
                               'tool_calls': budget.max_tool_calls - budget.tool_calls,
                               'seconds': budget.remaining_seconds()})
            label = f'{role}-v{version}-attempt{attempt + 1}'
            self.save(f'{label}-handoff.json', asdict(handoff))
            child = self.factory.create(role)
            self.node_count += 1
            self.parent.events.emit('node_started', role=role, child_run=child.events.id, attempt=attempt + 1)
            result = child.run(json.dumps(asdict(handoff), ensure_ascii=False),
                               max_steps=self.options.node_steps, max_output_chars=self.parent.output_limit,
                               system_prompt=self.factory.prompts[role])
            self.last_usage = result.last_usage
            self.save(f'{label}-response.json', {'status': result.status, 'answer': result.answer,
                                               'usage': child.used, 'run_id': child.events.id})
            self.parent.events.emit('node_finished', role=role, status=result.status, child_run=child.events.id)
            if result.status == 'cancelled':
                raise KeyboardInterrupt
            if result.status == 'budget_limit':
                raise BudgetExceeded(result.answer)
            try:
                if result.status != 'completed':
                    raise ValueError(f'Node ended with {result.status}')
                evidence = child.observed_evidence if role == 'researcher' else self.evidence
                data = validate(role, decode(result.answer), evidence)
                if role == 'researcher':
                    used = {item for fact in data['facts'] for item in fact['evidence_ids']}
                    self.evidence = {key: evidence[key] for key in sorted(used)}
                    self.parent.evidence.add(list(self.evidence.values()))
                return data
            except (ValueError, TypeError, KeyError) as error:
                feedback = str(error)
                self.failures.append({'role': role, 'attempt': attempt + 1, 'reason': feedback})
                self.parent.events.emit('node_failed', role=role, attempt=attempt + 1, reason=feedback)
        raise NodeFailure(f'{role}: {feedback}')

    def run(self, goal):
        baseline = dict(self.parent.session.usage)
        draft, review = '', None
        status, reason = 'partial', ''
        try:
            research = self.node('researcher', goal, {}, 1)
            self.save('research.json', research)
            self.save('evidence.json', self.evidence)
            draft = self.node('writer', goal, {'research': research, 'evidence': self.evidence}, 1)['draft']
            self.save('draft-v1.md', draft)
            for revision in range(self.options.max_revisions + 1):
                review = self.node('reviewer', goal, {'research': research, 'draft': draft, 'evidence': self.evidence}, revision + 1)
                self.save(f'review-v{revision + 1}.json', review)
                if review['passed']:
                    status = 'completed'
                    sources = []
                    for key in sorted(citations(draft)):
                        record = self.evidence[key]
                        sources.append(f"- [e:{key}] {record['uri']} — {record.get('locator', {})}")
                    draft += '\n\n## Sources\n\n' + '\n'.join(sources)
                    self.save('final.md', draft)
                    break
                if revision == self.options.max_revisions:
                    reason = 'Review still has unresolved issues after the revision limit'
                    break
                draft = self.node('reviser', goal, {'research': research, 'draft': draft, 'review': review,
                                                   'evidence': self.evidence}, revision + 2)['draft']
                self.save(f'draft-v{revision + 2}.md', draft)
        except NodeFailure as error:
            reason = str(error)
        except BudgetExceeded as error:
            status, reason = 'budget_limit', str(error)
        except (OSError, ValueError, TypeError) as error:
            reason = f'Workflow setup or persistence failed: {type(error).__name__}'
        except KeyboardInterrupt:
            self.parent.budget.cancel()
            status, reason = 'cancelled', 'Workflow cancelled'
        self.save('result.json', {'status': status, 'reason': reason, 'failures': self.failures,
                                 'review': review, 'usage': self.parent.used, 'nodes': self.node_count})
        total = {key: baseline[key] + self.parent.used[key] for key in baseline}
        result = RunResult(status, draft or reason, self.node_count, total, self.last_usage)
        self.parent.session.run_id = self.parent.events.id
        self.parent.session.append({'event': 'coordination', 'goal': goal, 'status': status,
                                    'reason': reason, 'artifacts': self.paths})
        self.parent.session.finish(result)
        self.parent.session.flush()
        self.parent.state.finish(status)
        self.parent.save()
        self.parent.events.emit('workflow_finished', status=status, reason=reason, usage=total)
        return result
