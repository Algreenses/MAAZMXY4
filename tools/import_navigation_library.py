"""Import real navigation crops from ZaoMengScript; never import user recordings.

This is an offline development tool. The installed agent uses only the emitted
Maa nodes, graph and images, and never needs the source project or Pillow.
"""
import argparse
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')


def inside(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('素材路径必须是相对路径。')
    logical = PurePosixPath(relative)
    if logical.is_absolute() or any(part in ('.','..') for part in logical.parts):
        raise ValueError('素材路径不能越界。')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('素材路径越界或不存在：' + str(relative))
    return path


def import_library(source, destination):
    source = Path(source).resolve() / 'assets/assistant'
    captures = source / 'captures'
    manifest_path, graph_path = captures / 'manifest.json', source / 'navigation.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    original = json.loads(graph_path.read_text(encoding='utf-8-sig'))
    width, height = original['width'], original['height']
    size = (width * 720 // min(width, height), height * 720 // min(width, height))
    scale = size[0] / width, size[1] / height
    frames = {item['id']: item for item in manifest['assets'] if item['role'] == 'client_frame'}
    pixels = {}
    privacy = json.loads((Path(__file__).with_name('navigation_privacy_masks.json')).read_text(encoding='utf8'))
    if privacy['version'] != 1 or privacy['base'] != list(size):
        raise ValueError('请重新审阅当前图片尺寸的隐私遮挡清单。')

    def frame(ident):
        if ident not in pixels:
            raw = inside(captures, frames[ident]['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != frames[ident]['sha256']:
                raise ValueError('源客户区截图校验失败：'+ident)
            image = Image.open(BytesIO(raw)).convert('RGB')
            if image.size != (width, height):
                raise ValueError('源客户区尺寸不一致：' + ident)
            scaled = image.resize(size, Image.Resampling.LANCZOS)
            mask = privacy['frames'].get(ident)
            if mask:
                if mask['source_sha256'] != frames[ident]['sha256']:
                    raise ValueError('截图已经改变，请重新审阅隐私遮挡：'+ident)
                draw = ImageDraw.Draw(scaled)
                for x,y,w,h in mask['boxes']:
                    draw.rectangle((x,y,x+w-1,y+h-1),fill=(32,32,32))
            pixels[ident] = scaled
        return pixels[ident]

    def rectangle(box):
        x, y, w, h = box
        left, top = int(x * scale[0]), int(y * scale[1])
        right, bottom = max(left + 1, int((x + w) * scale[0])), max(top + 1, int((y + h) * scale[1]))
        return [left, top, right - left, bottom - top]

    converted = {}
    for item in manifest['crops']:
        original_crop = inside(captures, item['path']).read_bytes()
        if hashlib.sha256(original_crop).hexdigest() != item['sha256']:
            raise ValueError('源裁剪素材校验失败：'+item['id'])
        x, y, w, h = rectangle(item['bbox'])
        crop = frame(item['parent_asset']).crop((x, y, x + w, y + h))
        relative = Path('navigation') / item['path']
        target = destination / 'assets/resource/base/image' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        crop.save(target)
        converted[item['id']] = dict(id=item['id'], name=item['name'], path=relative.as_posix(),
            bbox=[x, y, w, h], source_bbox=item['bbox'], source_sha256=item['sha256'],
            sha256=hashlib.sha256(target.read_bytes()).hexdigest(), parent=item['parent_asset'],
            role=item['role'], action_policy=item.get('action_policy', 'recognize_only'))
        converted[item['id']]['redacted'] = any(x < rx+rw and rx < x+w and y < ry+rh and ry < y+h
            for rx,ry,rw,rh in privacy['frames'].get(item['parent_asset'],{}).get('boxes',[]))

    pause_box = [638, 343, 132, 34]
    pause_relative = 'navigation/panels/daily-pause-caption.png'
    pause_path = destination / 'assets/resource/base/image' / pause_relative
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    x, y, w, h = pause_box
    frame('capture-daily-expanded-client').crop((x, y, x+w, y+h)).save(pause_path)
    write_json(destination / 'agent/data/navigation/pause-sources.json', [dict(
        source='capture-daily-expanded-client', source_sha256=frames['capture-daily-expanded-client']['sha256'],
        base=list(size), bbox=pause_box, path=pause_relative,
        sha256=hashlib.sha256(pause_path.read_bytes()).hexdigest())])
    pause_recognition = dict(type='TemplateMatch', param=dict(template=[
        'bounty-candle/pause.png', 'bounty-candle/pause-cloudy.png', pause_relative], threshold=.9))
    pipeline, probes, missing = {}, {}, {}
    for key, definition in original['probes'].items():
        name = 'Nav_Probe_' + key
        if 'reference' not in definition:
            detector = definition['detector']
            if detector == 'navigation_paused':
                recognition = deepcopy(pause_recognition)
            elif detector == 'hub_scene':
                # Reuse the new project's verified character-map detector.
                # This does not restore the retired Sumeru noticeboard route.
                recognition = dict(type='TemplateMatch', param=dict(template='任务.png'))
            elif detector == 'daily_menu_scene':
                # Both controls are outside the pause caption. The state also
                # excludes paused_overlay, so resuming can locate this menu
                # without requiring a machine-local detector.
                parts = []
                for key_name in ('daily_expanded_arhat', 'daily_expanded_arena'):
                    asset = converted[original['probes'][key_name]['reference']]
                    parts.append(dict(recognition=dict(type='TemplateMatch', param=dict(
                        template=asset['path'], roi=asset['bbox'], threshold=.94))))
                recognition = dict(type='And', param=dict(all_of=parts))
            elif detector == 'home_label':
                # Official named screenshots identify this exact Buddha
                # landmark as Sumeru. Match the user's current pixels rather
                # than shipping an old annotated web screenshot or a blind XY.
                asset = converted['capture-world-sumeru-entry']
                recognition = dict(type='TemplateMatch', param=dict(
                    template=asset['path'], roi=asset['bbox'], threshold=.94))
            else:
                missing[key] = detector
                continue
        else:
            asset = converted[definition['reference']]
            if asset['redacted']:
                raise ValueError('导航探针不能使用个人信息区域：'+key)
            recognition = dict(type='TemplateMatch', param=dict(template=asset['path'],
                roi=asset['bbox'], method=definition.get('method', 5), threshold=definition.get('threshold', .94)))
        pipeline[name] = dict(recognition=recognition, action=dict(type='DoNothing'), next=[], post_delay=0)
        probes[key] = [name]
        if 'color_guard' in definition:
            guard = deepcopy(definition['color_guard'])
            x, y, _w, _h = rectangle(guard['roi'])
            guard['roi'] = [x, y, 2, 2]
            asset = converted[definition['reference']]
            sample_image = frame(asset['parent'])
            sample = [sample_image.getpixel((left, top)) for left in range(x, x+2) for top in range(y, y+2)]
            guard['lower'] = [max(0, min(p[c] for p in sample) - 18) for c in range(3)]
            guard['upper'] = [min(255, max(p[c] for p in sample) + 18) for c in range(3)]
            guard_name = name + '_Color'
            pipeline[guard_name] = dict(recognition=dict(type='ColorMatch', param=guard),
                action=dict(type='DoNothing'), next=[], post_delay=0)
            probes[key].append(guard_name)

    raw_nodes = {item['id']: item for item in original['nodes']}
    nodes, unavailable = [], {}
    for item in original['nodes']:
        absent = [p for p in item['recognition'] + item.get('exclude', []) if p not in probes]
        if absent:
            unavailable[item['id']] = absent
            continue
        nodes.append(dict(id=item['id'], label=item['label'],
            recognition=[n for p in item['recognition'] for n in probes[p]],
            exclude=[probes[p][0] for p in item.get('exclude', [])], obscures=[]))
    ids = {node['id'] for node in nodes}
    for node in nodes:
        hidden, pending = set(), list(raw_nodes[node['id']].get('obscures', []))
        while pending:
            ident = pending.pop()
            if ident not in hidden:
                hidden.add(ident)
                pending.extend(raw_nodes[ident].get('obscures', []))
        node['obscures'] = sorted(hidden & ids)
    # The original hub required machine-local calibration. The current task
    # icon provides a packaged, generic map guard for entering/closing panels.
    def state_id(ident):
        return 'task_map' if ident == 'hub' else ident
    for node in nodes:
        node['id'] = state_id(node['id'])
        node['obscures'] = [state_id(ident) for ident in node['obscures']]
        if node['id'] == 'task_map':
            node['label'] = '人物与主线地图'
    ids = {node['id'] for node in nodes}
    edges = []
    for index, item in enumerate(original['edges']):
        source_id, target_id = state_id(item['source']), state_id(item['target'])
        if source_id not in ids or target_id not in ids or any(s['probe'] not in probes for s in item['steps']):
            continue
        # The old noticeboard remains recognizable/closable, but never becomes
        # a route into a bounty. Accepted quests use the current task panel.
        if item['target'] == 'bounty_board':
            continue
        sequence = [(p, False, 0) for p in next(n for n in nodes if n['id'] == source_id)['recognition']]
        for step in item['steps']:
            if step['click'] and 'reference' in original['probes'][step['probe']]:
                if converted[original['probes'][step['probe']]['reference']]['action_policy'] == 'recognize_only':
                    raise ValueError('只读素材不能作为点击动作：' + step['probe'])
            sequence += [(p, False, 0) for p in probes[step['probe']][1:]]
            sequence.append((probes[step['probe']][0], step['click'], step.get('post_delay', 300)))
        names = [f'Nav_Edge_{index:03d}_{i:02d}' for i in range(len(sequence))]
        for i, (probe, click, delay) in enumerate(sequence):
            node = deepcopy(pipeline[probe])
            node.update(next=names[i+1:i+2], timeout=item['timeout_ms'], post_delay=delay)
            if click:
                node['action'] = dict(type='Click', param=dict(target=True))
            pipeline[names[i]] = node
        edge = {k: deepcopy(item[k]) for k in ('id', 'source', 'target', 'label', 'cost', 'timeout_ms')}
        edge.update(source=source_id, target=target_id, entry=names[0], max_attempts=2)
        edges.append(edge)
    # The source library's pause wording differs from the current project's
    # standard pause caption. Both caption and menu controls must match before
    # restoring this known menu; no blind click on an unknown paused screen.
    pipeline['Nav_ResumeDaily'] = dict(recognition=dict(type='And', param=dict(all_of=[
        dict(recognition=deepcopy(pause_recognition)),
        dict(recognition=deepcopy(pipeline['Nav_Probe_daily_menu_scene']['recognition']))], box_index=0)),
        action=dict(type='Click', param=dict(target=True)), next=[], timeout=10000, post_delay=350)
    edges.append(dict(id='daily_expanded__resume', source='daily_expanded', target='daily_menu',
        label='恢复已确认的日常活动菜单', entry='Nav_ResumeDaily', cost=1, timeout_ms=10000, max_attempts=2))
    graph = dict(version=1, targets=['world'], nodes=nodes, edges=edges,
        max_steps=30, max_seconds=120, settle_ms=300, locate_attempts=3)
    write_json(destination / 'agent/data/navigation/graph.json', graph)
    write_json(destination / 'agent/data/navigation/resources.json', dict(version=1,
        pipelines=['navigation.json', 'battle_outcomes.json']))
    write_json(destination / 'agent/data/navigation/source-catalog.json', original)
    write_json(destination / 'agent/data/navigation/library.json', dict(version=1, base=list(size),
        source_project='ZaoMengScript', source_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        source_graph_sha256=hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        source_node_count=len(original['nodes']), source_edge_count=len(original['edges']),
        source_probe_count=len(original['probes']), assets=list(converted.values()),
        privacy_masks={ident:entry['boxes'] for ident,entry in privacy['frames'].items()},
        unavailable=unavailable, missing_detectors=missing,
        notice='游戏画面版权归4399及相关权利人；素材来自用户提供的真实截图，仅作界面识别参考，候选路径仍需实机验证。'))
    write_json(destination / 'assets/resource/base/pipeline/navigation.json', pipeline)
    # Native fixtures have no Windows title bar, and use the exact runtime size.
    for ident, item in frames.items():
        target = destination / 'tests/fixtures/navigation' / Path(item['path']).name
        target.parent.mkdir(parents=True, exist_ok=True)
        frame(ident).save(target)
    print(f'已迁移 {len(converted)} 份素材，{len(nodes)} 个可定位界面、{len(edges)} 条候选路径。')


def import_task_panel(destination):
    """Add a guarded close action from the existing user-supplied real frame."""
    source = ROOT / 'tests/fixtures/navigation/quest-taizong-real.png'
    raw = source.read_bytes()
    image = Image.open(BytesIO(raw)).convert('RGB')
    if image.size != (1147, 720):
        raise ValueError('任务面板来源需保持1147×720客户区。')
    box = [916, 62, 65, 68]
    x, y, w, h = box
    relative = 'navigation/panels/quest-close.png'
    target = destination / 'assets/resource/base/image' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    image.crop((x, y, x+w, y+h)).save(target)
    path = destination / 'assets/resource/base/pipeline/battle_outcomes.json'
    definitions = json.loads(path.read_text(encoding='utf-8-sig'))
    for name in ('Nav_PausedTaskMap', 'Nav_ResumeTaskMap'):
        definitions[name]['recognition']['param']['all_of'][0]['recognition'] = dict(
            type='TemplateMatch', param=dict(template=['bounty-candle/pause.png',
                'bounty-candle/pause-cloudy.png', 'navigation/panels/daily-pause-caption.png'], threshold=.9))
    definitions['Nav_CloseQuest'] = dict(recognition=dict(type='And', param=dict(all_of=[
        dict(recognition=dict(type='TemplateMatch', param=dict(template=relative,
            roi=[860, 20, 180, 150], threshold=.9))),
        dict(recognition=deepcopy(definitions['Nav_QuestPanel']['recognition'])),
    ], box_index=0)), action=dict(type='Click', param=dict(target=True)),
        next=[], timeout=10000, post_delay=350)
    write_json(path, definitions)
    write_json(destination / 'agent/data/navigation/task-panel-sources.json', [dict(
        source='user-provided-log/quest-taizong-real.png', source_sha256=hashlib.sha256(raw).hexdigest(),
        base=[1147, 720], bbox=box, path=relative, sha256=hashlib.sha256(target.read_bytes()).hexdigest())])


def import_outcomes(failed, succeeded, destination):
    definitions, provenance = {}, []
    specifications = [
        ('failed', failed, [330, 292, 480, 133], [600, 490, 180, 60]),
        ('succeeded', succeeded, [570, 169, 315, 90], [810, 530, 210, 65]),
    ]
    for kind, source, title, button in specifications:
        image = Image.open(source).convert('RGB')
        if image.size != (1177, 777):
            raise ValueError('本批通关素材需要1177×777原始窗口截图。')
        frame = image.crop((1, 39, 1176, 776)).resize((1147, 720), Image.Resampling.LANCZOS)
        fixture = destination / f'tests/fixtures/navigation/challenge-{kind}.png'
        fixture.parent.mkdir(parents=True, exist_ok=True)
        frame.save(fixture)
        for label, box in [('title', title), ('return', button)]:
            x, y, w, h = box
            path = destination / f'assets/resource/base/image/navigation/outcomes/{kind}-{label}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.crop((x, y, x+w, y+h)).save(path)
            provenance.append(dict(kind=kind, part=label, source_sha256=hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                client_crop=[1, 39, 1175, 737], base=[1147, 720], bbox=box,
                path=path.relative_to(destination / 'assets/resource/base/image').as_posix(),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        suffix = kind.capitalize()
        title_recognition = dict(type='TemplateMatch', param=dict(template=f'navigation/outcomes/{kind}-title.png',
            roi=[200, 120, 780, 380], threshold=.9))
        definitions['Result_'+suffix] = dict(recognition=title_recognition,
            action=dict(type='DoNothing'), next=[], post_delay=0)
        definitions['Result_'+suffix+'Return'] = dict(recognition=dict(type='And', param=dict(all_of=[
            dict(recognition=dict(type='TemplateMatch', param=dict(template=f'navigation/outcomes/{kind}-return.png',
                roi=[500, 450, 550, 200], threshold=.85))), dict(recognition=title_recognition)], box_index=0)),
            action=dict(type='Click', param=dict(target=True)), next=[], timeout=10000, post_delay=500)
    # Existing current-project assets supply the task button; the graph handles
    # panel-over-map disambiguation before an edge is considered executable.
    definitions['Nav_TaskMap'] = dict(recognition=dict(type='TemplateMatch', param=dict(template='任务.png')),
        action=dict(type='DoNothing'), next=[], post_delay=0)
    definitions['Nav_QuestPanel'] = dict(recognition=dict(type='And', param=dict(all_of=[
        dict(recognition=dict(type='OCR', param=dict(expected=['^任务$'], roi=[450, 20, 280, 100]))),
        dict(recognition=dict(type='OCR', param=dict(expected=['悬赏任务'], roi=[180, 85, 500, 100]))),
    ])), action=dict(type='DoNothing'), next=[], post_delay=0)
    definitions['Nav_AcceptedBounties'] = dict(recognition=dict(type='OCR',
        param=dict(expected=['完成悬赏任务'], roi=[180, 130, 350, 100])),
        action=dict(type='DoNothing'), next=[], post_delay=0)
    definitions['Nav_OpenQuest'] = dict(recognition=deepcopy(definitions['Nav_TaskMap']['recognition']),
        action=dict(type='Click', param=dict(target=True)), next=[], timeout=10000, post_delay=350)
    pause = dict(type='And',param=dict(all_of=[
        dict(recognition=dict(type='TemplateMatch',param=dict(template='bounty-candle/pause.png',threshold=.9))),
        dict(recognition=deepcopy(definitions['Nav_TaskMap']['recognition']))],box_index=0))
    definitions['Nav_PausedTaskMap']=dict(recognition=pause,action=dict(type='DoNothing'),next=[],post_delay=0)
    definitions['Nav_ResumeTaskMap']=dict(recognition=deepcopy(pause),action=dict(type='Click',param=dict(target=True)),
        next=[],timeout=10000,post_delay=350)
    definitions['Nav_SelectBountyTab'] = dict(recognition=dict(type='OCR',
        param=dict(expected=['^悬赏任务$'], roi=[180, 85, 500, 100])),
        action=dict(type='Click', param=dict(target=True)), next=[], timeout=10000, post_delay=350)
    definitions['Nav_BlockedDialog'] = dict(recognition=dict(type='OCR',
        param=dict(expected=['验证码|请输入密码|账号密码登录|扫码登录|背包已满|网络连接.*断|连接.*失败|余额不足'])),
        action=dict(type='DoNothing'), next=[], post_delay=0)
    write_json(destination / 'assets/resource/base/pipeline/battle_outcomes.json', definitions)
    write_json(destination / 'agent/data/navigation/outcome-sources.json', provenance)
    print('已从两张真实截图生成通关标题、返回按钮及离线正反例。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT)
    parser.add_argument('--failed-image', type=Path)
    parser.add_argument('--succeeded-image', type=Path)
    args = parser.parse_args()
    import_library(args.source, args.destination.resolve())
    if bool(args.failed_image) != bool(args.succeeded_image):
        parser.error('成功与失败截图必须一起提供。')
    if args.failed_image:
        import_outcomes(args.failed_image, args.succeeded_image, args.destination.resolve())
    import_task_panel(args.destination.resolve())
