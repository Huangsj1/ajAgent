"""Deterministic coordination failures, evidence contracts and shared budgets."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from ajagent.multi_agent.coordinator import Coordinator
from ajagent.multi_agent.handoff import validate
from ajagent.multi_agent.roles import CoordinationOptions, RoleFactory
from ajagent.tools.common.evidence import source_record
from ajagent.harness.trace import FakeModel
from ajagent.harness.budget import Budget
from test_harness import runner, call, final

ROOT = Path(__file__).resolve().parents[1]


def setup(tmp_path, reviews=None, research=None, options=None):
    (tmp_path / 'facts.txt').write_text('Current retention is 6 hours.')
    parent = runner(tmp_path, [])
    settings = SimpleNamespace(config_path=ROOT / 'config/config.local.toml', provider='deepseek', model='fake',
                               context_tokens=32000, output_tokens=4096)
    source = source_record({'path':'facts.txt', 'start_line':1})
    eid = source['id']
    facts = {'facts':[{'claim':'6 hours', 'evidence_ids':[eid]}], 'unresolved':[], 'conflicts':[]}
    draft = {'draft':f'Retention: 6 hours [e:{eid}]'}
    reviews = iter(reviews or [{'passed':True,'issues':[]}])
    children = []
    def factory(role, selected):
        turns = [final(json.dumps(draft))]
        if role == 'researcher':
            turns = [call('read_file', {'path':'facts.txt'}), final(json.dumps(research if research is not None else facts))]
        if role == 'reviewer':
            turns = [final(json.dumps(next(reviews)))]
        return FakeModel(turns)
    roles = RoleFactory(parent, settings, options or CoordinationOptions(), factory)
    create = roles.create
    def tracked(role):
        child = create(role)
        children.append((role, child))
        return child
    roles.create = tracked
    return Coordinator(parent, roles, options or CoordinationOptions()), parent, children


def test_review_failure_revision_pass_and_versioned_outputs(tmp_path):
    fail = {'passed':False,'issues':[{'location':'Retention','problem':'Needs wording','suggestion':'Clarify current version'}]}
    workflow, parent, children = setup(tmp_path, [fail, {'passed':True,'issues':[]}])
    result = workflow.run('Explain retention')
    assert result.status == 'completed'
    assert [role for role, _ in children] == ['researcher','writer','reviewer','reviser','reviewer']
    assert (workflow.directory / 'draft-v1.md').exists() and (workflow.directory / 'draft-v2.md').exists()
    assert (workflow.directory / 'final.md').read_text() == result.answer
    assert result.usage == parent.used and parent.budget.tokens == sum(result.usage.values())
    assert len({child.session.id for _,child in children}) == 5
    assert all('write_file' not in child.registry and 'apply_proposal' not in child.registry for _,child in children)
    assert all(child.parent is parent for _,child in children)


def test_empty_research_retries_once_then_partial(tmp_path):
    workflow, _, children = setup(tmp_path, research={'facts':[], 'unresolved':['missing'], 'conflicts':[]})
    result = workflow.run('Unknown fact')
    assert result.status == 'partial' and len(children) == 2
    assert not (workflow.directory / 'final.md').exists()
    assert len(workflow.failures) == 2


def test_revision_limit_returns_latest_draft_and_issues(tmp_path):
    fail = {'passed':False,'issues':[{'location':'all','problem':'incomplete','suggestion':'fix'}]}
    workflow, _, _ = setup(tmp_path, [fail]*3)
    result = workflow.run('Explain')
    assert result.status == 'partial' and 'Retention' in result.answer
    assert (workflow.directory / 'draft-v3.md').exists()
    assert not (workflow.directory / 'final.md').exists()
    assert json.loads((workflow.directory / 'result.json').read_text())['review']['issues']


def test_parent_budget_stops_nodes(tmp_path):
    workflow, parent, children = setup(tmp_path)
    parent.budget = Budget(10, 1, 30)
    result = workflow.run('Explain')
    assert result.status == 'budget_limit' and len(children) == 1
    assert not (workflow.directory / 'final.md').exists()


def test_citation_and_review_validation():
    with pytest.raises(ValueError):
        validate('writer', {'draft':'Invented [e:fake]'}, {'real':{}})
    with pytest.raises(ValueError):
        validate('reviewer', {'passed':True,'issues':[{'location':'x','problem':'x','suggestion':'x'}]}, {})
    research = {'facts':[{'claim':'x','evidence_ids':['a']},{'claim':'x','evidence_ids':['b']}], 'unresolved':[], 'conflicts':['different versions']}
    assert validate('researcher', research, {'a':{},'b':{}})['facts'] == [{'claim':'x','evidence_ids':['a','b']}]


def test_invalid_role_options():
    for data in ({'max_revisions':3}, {'node_retries':2}, {'roles':{'writer':{'provider':'other'}}}):
        with pytest.raises(ValueError):
            CoordinationOptions.load({'coordination':data})


def test_researcher_delegation_inherits_root_budget_and_no_recursive_tool(tmp_path):
    from ajagent.tools.delegation.agent import delegation_tool
    from ajagent.tools.common.base import ToolError
    from ajagent.core.types import ToolCall
    from ajagent.harness.trace import FakeModel
    workflow, parent, _ = setup(tmp_path)
    parent.registry['delegate'] = delegation_tool(parent)
    researcher = workflow.factory.create('researcher')
    assert 'delegate' in researcher.registry
    assert 'delegate' not in workflow.factory.create('reviewer').registry
    with pytest.raises(ToolError, match='Children cannot delegate'):
        delegation_tool(researcher).handler(contract={'goal':'x'})
    contract = {'goal':'Read facts.txt', 'context':'', 'acceptance_criteria':['cite source'], 'allowed_paths':['facts.txt']}
    seen = []
    def model():
        child = FakeModel([call('read_file', {'path':'facts.txt'}), final('6 hours')])
        generate = child.generate
        def observe(context, tools):
            seen.append([tool.name for tool in tools])
            return generate(context, tools)
        child.generate = observe
        return child
    researcher.registry['delegate'] = delegation_tool(researcher, model, allow_nested=True)
    calls = [ToolCall(str(i),'delegate',json.dumps({'contract':contract})) for i in range(2)]
    results = list(researcher.execute_many(calls))
    assert all(result.ok for result in results)
    assert all('delegate' not in tools and 'write_file' not in tools for tools in seen)
    assert parent.used == researcher.used and parent.used['input_tokens'] > 0
    assert parent.budget.tool_calls == 4
    assert len(researcher.observed_evidence) == 1


def test_cancelled_workflow_is_not_published(tmp_path):
    workflow, parent, children = setup(tmp_path)
    parent.budget.cancel()
    result = workflow.run('Explain')
    assert result.status == 'cancelled' and not children
    assert not (workflow.directory / 'final.md').exists()


def test_invalid_writer_retries_with_feedback_then_stops(tmp_path):
    workflow, _, children = setup(tmp_path)
    factory = workflow.factory.model_factory
    workflow.factory.model_factory = lambda role, selected: FakeModel([final('not JSON')]) if role == 'writer' else factory(role, selected)
    result = workflow.run('Explain')
    assert result.status == 'partial'
    assert [role for role,_ in children] == ['researcher','writer','writer']
    handoff = json.loads((workflow.directory / 'writer-v1-attempt2-handoff.json').read_text())
    assert handoff['inputs']['retry_feedback']
    assert not (workflow.directory / 'final.md').exists()
