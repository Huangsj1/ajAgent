"""Small explicit contracts between isolated role contexts."""
from dataclasses import dataclass
import json
import re


@dataclass
class Handoff:
    task_id: str
    goal: str
    inputs: dict
    evidence_ids: list[str]
    artifact_paths: list[str]
    acceptance_criteria: list[str]
    remaining_budget: dict


def decode(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def citations(text):
    return set(re.findall(r'\[e:([^\]]+)\]', text))


def validate(role, value, evidence):
    if role == 'researcher':
        facts = value.get('facts')
        if not isinstance(facts, list) or not facts:
            raise ValueError('Research needs nonempty facts with evidence')
        for fact in facts:
            if not isinstance(fact, dict) or not isinstance(fact.get('claim'), str) or not fact['claim'].strip():
                raise ValueError('Each fact needs a claim')
            ids = fact.get('evidence_ids')
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in evidence for i in ids):
                raise ValueError('Fact references evidence not observed by this researcher')
        for key in ('unresolved', 'conflicts'):
            if not isinstance(value.get(key), list) or any(not isinstance(x, str) for x in value[key]):
                raise ValueError(f'{key} must be a list of strings')
        # Exact duplicate claims are merged; semantic disagreement stays explicit.
        unique = {}
        for fact in facts:
            item = unique.setdefault(fact['claim'].strip(), {'claim': fact['claim'].strip(), 'evidence_ids': []})
            item['evidence_ids'] = sorted(set(item['evidence_ids']) | set(fact['evidence_ids']))
        value['facts'] = list(unique.values())
    elif role == 'reviewer':
        issues = value.get('issues')
        if type(value.get('passed')) is not bool or not isinstance(issues, list):
            raise ValueError('Review needs passed and issues')
        for issue in issues:
            if not isinstance(issue, dict) or any(not isinstance(issue.get(k), str) or not issue[k].strip() for k in ('location','problem','suggestion')):
                raise ValueError('Each issue needs location, problem and suggestion')
        if value['passed'] != (not issues):
            raise ValueError('Review pass must agree with issue list')
    else:
        draft = value.get('draft')
        if not isinstance(draft, str) or not draft.strip():
            raise ValueError('Empty draft')
        used = citations(draft)
        if not used or used - evidence.keys():
            raise ValueError('Draft needs traceable [e:ID] citations')
    return value
