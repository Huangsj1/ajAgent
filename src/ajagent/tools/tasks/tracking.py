"""Tools for the main agent's dynamic, durable task plan."""
from ajagent.tools.common.base import ToolSpec, schema


def task_tools(progress):
    limits = progress.options
    text = {'type':'string', 'minLength':1, 'maxLength':limits.text_chars}
    key = {'type':'string', 'pattern':'^[A-Za-z0-9_-]{1,80}$'}
    argv = {'type':'array', 'minItems':1, 'maxItems':progress.workspace.options.command_max_args,
            'items':{'type':'string', 'maxLength':progress.workspace.options.command_arg_chars}}
    task = schema({'id':key, 'title':text, 'acceptance':text,
                   'paths':{'type':'array','minItems':1,'maxItems':limits.max_paths,'items':text},
                   'checks':{'type':'array','minItems':1,'maxItems':limits.max_checks,'items':argv}},
                  ['id','title','acceptance','paths','checks'])
    return [
        ToolSpec('plan_tasks', 'Create a durable plan for a substantial task. Each objective needs real executable acceptance checks and output files. Do not plan trivial tasks.',
                 schema({'goal':text,'tasks':{'type':'array','minItems':1,'maxItems':limits.max_tasks,'items':task}},['goal','tasks']), progress.create, 'write'),
        ToolSpec('task_status', 'Read current plan; invalidate passed objectives whose outputs changed.', schema({},[]), progress.status, parallel=False),
        ToolSpec('start_task', 'Work on one objective at a time. Unchanged passed objectives cannot restart.', schema({'task_id':key},['task_id']), progress.start, 'write'),
        ToolSpec('update_task_paths', 'Update an unfinished objective when its file layout changes. Acceptance criteria and check commands stay unchanged; explain the reason.',
                 schema({'task_id':key, 'paths':{'type':'array','minItems':1,'maxItems':limits.max_paths,'items':text}, 'reason':text},
                        ['task_id','paths','reason']), progress.update_paths, 'write'),
        ToolSpec('checkpoint_task', 'Save factual progress, artifacts and the concrete next action before ending a work segment.',
                 schema({'summary':text,'next_step':text},['summary','next_step']), progress.checkpoint, 'write'),
        ToolSpec('verify_task', 'Run the active objective\'s declared checks through run_command and persist actual results. Only exit 0 plus unchanged required files marks passed.',
                 schema({'summary':text},['summary']), progress.verify, 'execute'),
    ]
