"""Plan old playback-option migration without modifying user files or guessing scripts."""
from copy import deepcopy
from pathlib import Path

from script_library import resolve_recording_reference
from task_catalog import custom_parameters, _playback_nodes, inspect_saved_recording, task_key


def _case_files(case, playback_nodes):
    overrides = case.get('pipeline_override', {})
    if not isinstance(overrides, dict): return set()
    references = set()
    for node, definition in overrides.items():
        if playback_nodes and node not in playback_nodes: continue
        parameters = custom_parameters(definition)
        if isinstance(parameters, dict) and isinstance(parameters.get('file'), str):
            references.add(parameters['file'])
    return references


def plan_legacy_bindings(root, old_interface, tasks, new_nodes, instances, bindings, old_nodes=None):
    """Return proposed bindings and evidence; caller commits with its existing backup transaction.

    Conflicting per-instance choices cannot be represented by the shared binding
    model. Refuse the migration before any write rather than selecting a winner.
    """
    root = Path(root)
    library = root / 'recordings'
    result, changes = deepcopy(bindings), []
    old_tasks = {task['name']: task for task in old_interface.get('task', [])}
    new_tasks = {task['name']: task for task in tasks}
    proposals = {}
    for instance_path, instance in instances:
        for item in instance.get('TaskItems', []):
            if not isinstance(item, dict): continue
            task = new_tasks.get(item.get('name'))
            old_task = old_tasks.get(item.get('name'))
            if task is None or old_task is None: continue
            parameters = custom_parameters(new_nodes.get(task['entry'], {}))
            execution = parameters.get('execution', {}) if isinstance(parameters, dict) else {}
            ident = execution.get('binding_id') or task_key(task['name'], task['entry'])
            if ident in bindings: continue  # Explicit existing user bindings always win.
            if not execution and not _playback_nodes(new_nodes, task['entry']): continue
            playback = _playback_nodes(old_nodes or {}, old_task['entry'])
            references = set()
            for choice in item.get('option', []):
                if not isinstance(choice, dict) or choice.get('name') in task.get('option', []): continue
                schema = old_interface.get('option', {}).get(choice.get('name'), {})
                cases = schema.get('cases', [])
                index = choice.get('index')
                if type(index) is not int or not 0 <= index < len(cases):
                    index = next((i for i, case in enumerate(cases) if case.get('name') == schema.get('default_case')), None)
                if index is not None: references.update(_case_files(cases[index], playback))
            references -= {'', '?', '？'}  # An old manual picker does not identify a saved file.
            if not references: continue
            if len(references) != 1:
                raise ValueError(f'任务“{task["name"]}”包含多份旧回放选择，请先明确绑定；配置未修改：{instance_path}')
            reference = references.pop()
            previous_reference = reference
            if reference == '@latest':
                files = sorted((p for p in library.glob('*.json') if not p.name.startswith('.')), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
                if not files: continue  # No previous recording exists; retain the explicit bind requirement.
                reference = files[0].name
            elif reference == '@selected':
                reference = resolve_recording_reference(library, reference).name
            # Validate with exactly the same native playback contract as new bindings.
            recording = inspect_saved_recording(root, reference)
            filename = recording['file']
            if ident in proposals and proposals[ident]['file'] != filename:
                raise ValueError(f'任务“{task["name"]}”在不同配置中选择了不同脚本，不能自动合并为共享绑定；配置未修改。')
            proposals[ident] = dict(file=filename, mode='pipeline')
            changes.append(dict(task_id=ident, task=task['name'], file=filename, instance=instance_path, previous_reference=previous_reference))
    result.update(proposals)
    return result, changes
