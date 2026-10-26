"""Discover tasks and script insertion points from this project's existing data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from script_library import read_binding_records, resolve_recording_reference
from script_studio.model import RecordingDocument, MAX_FILE_BYTES


def task_key(name, entry):
    return 'task-' + hashlib.sha256((name + '\0' + entry).encode('utf8')).hexdigest()[:32]


def read_document(path):
    # Same comment grammar as the repository's schema validator. Keep quoted
    # URLs and escape sequences intact while accepting resource JSONC files.
    source = path.read_text(encoding='utf-8-sig')
    result, index, quoted, escaped = [], 0, False, False
    while index < len(source):
        char = source[index]
        if quoted:
            result.append(char)
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif source[index:index+2] == '//':
            index = source.find('\n', index)
            if index < 0: break
            result.append('\n')
        elif source[index:index+2] == '/*':
            end = source.find('*/', index+2)
            if end < 0: raise ValueError('未闭合JSON注释：' + str(path))
            result.append(' ')
            index = end + 1
        else:
            result.append(char)
        index += 1
    value = json.loads(''.join(result))
    if not isinstance(value, dict): raise ValueError('任务配置必须是对象：' + str(path))
    return value


def merge(base, overlay):
    result = deepcopy(base)
    for key, value in overlay.items():
        result[key] = merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else deepcopy(value)
    return result


def _inside(root, value):
    path = (root / value.replace('{PROJECT_DIR}', str(root))).resolve()
    if not path.is_relative_to(root): raise ValueError('任务资源不能指向工程外部。')
    return path


def _configuration(root):
    path = next((root / name for name in ('interface.json', 'interface.jsonc', 'assets/interface.json') if (root / name).is_file()), None)
    if path is None: return {}, {}
    interface = read_document(path)
    imports = interface.get('import', [])
    for reference in [imports] if isinstance(imports, str) else imports:
        extra = read_document(_inside(root, reference))
        interface['task'] = [*interface.get('task', []), *extra.get('task', [])]
    nodes = {}
    for resource in interface.get('resource', []):
        paths = resource.get('path', [])
        for relative in [paths] if isinstance(paths, str) else paths:
            folder = _inside(root, relative) / 'pipeline'
            if not folder.is_dir() and path.parent.name == 'assets':
                folder = _inside(root, 'assets/' + relative.lstrip('./')) / 'pipeline'
            if folder.is_dir():
                for source in sorted(folder.rglob('*')):
                    if source.suffix in ('.json', '.jsonc'):
                        nodes.update(read_document(source))
    return interface, nodes


def _playback_nodes(nodes, entry):
    pending, visited, matches = [entry], set(), []
    while pending:
        name = pending.pop()
        if name in visited or name not in nodes: continue
        visited.add(name)
        node = nodes[name]
        if _action_parameters(node).get('custom_action') == 'battle_play':
            matches.append(name)
        for field in ('next', 'on_error'):
            links = node.get(field, [])
            if isinstance(links, str): links = [links]
            pending.extend(link for link in links if isinstance(link, str))
    return sorted(matches)


def _action_parameters(node):
    action = node.get('action', {})
    return action.get('param', {}) if isinstance(action, dict) else node


def task_catalog(root):
    root = Path(root).resolve()
    interface, nodes = _configuration(root)
    definitions = list(interface.get('task', []))
    for source in sorted((root / 'config/instances').glob('*.json')):
        definitions += read_document(source).get('TaskItems', [])
    bindings = read_binding_records(root / 'recordings')
    tasks, seen = [], set()
    for definition in definitions:
        if not isinstance(definition, dict): continue
        name, entry = definition.get('name'), definition.get('entry')
        if not isinstance(name, str) or not isinstance(entry, str) or (name, entry) in seen: continue
        seen.add((name, entry))
        graph = merge(nodes, definition.get('pipeline_override', {}) or {})
        parameters = _action_parameters(graph.get(entry, {})).get('custom_action_param', {})
        profile = None
        if isinstance(parameters, dict) and isinstance(parameters.get('execution'), dict):
            spec = parameters['execution']
            resume = spec.get('resume_entry')
            resume_parameters = _action_parameters(graph.get(resume, {})).get('custom_action_param', {})
            profile = dict(entry=entry, resume_entry=resume, parameters=deepcopy(parameters),
                           resume_parameters=deepcopy(resume_parameters))
        ident = parameters.get('execution', {}).get('binding_id') if profile else None
        ident = ident or task_key(name, entry)
        playback = _playback_nodes(graph, entry)
        modes = ['pipeline', 'replace'] if playback else ['replace']
        binding = bindings.get(ident)
        tasks.append(dict(id=ident, name=name, label=definition.get('label') or name,
                          entry=entry, playback_nodes=playback, modes=modes,
                          playback_parameters={node: deepcopy(_action_parameters(graph[node]).get('custom_action_param', {})) for node in playback},
                          binding=deepcopy(binding), profile=profile))
    return tasks


def inspect_saved_recording(root, reference):
    path = resolve_recording_reference(Path(root) / 'recordings', reference)
    with path.open('rb') as stream: raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES: raise ValueError('脚本不能超过16MiB。')
    data = json.loads(raw.decode('utf-8-sig'))
    document = RecordingDocument.from_dict(data)
    document.to_dict(strict=True)
    if not data.get('actions'): raise ValueError('任务不能绑定空白脚本。')
    if document.diagnostics: raise ValueError('请先修复脚本中的操作诊断。')
    if min(document.base) != 720: raise ValueError('请先将脚本转换为本工程720坐标。')
    return dict(file=path.name, path=str(path), sha256=hashlib.sha256(raw).hexdigest(), base=list(document.base))


def execution_plan(root, name, entry):
    task = next((item for item in task_catalog(root) if item['name'] == name and item['entry'] == entry), None)
    if task is None: return dict(handled=False)
    binding = task['binding']
    if binding:
        if binding['mode'] not in task['modes']: raise ValueError('任务已没有脚本回放节点，请重新选择绑定方式。')
        task['recording'] = inspect_saved_recording(root, binding['file'])
    task['handled'] = bool(binding or task['profile'])
    return task
