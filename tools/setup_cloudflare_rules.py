# -*- coding: utf-8 -*-
"""
一次配置 Cloudflare 上 MAA 更新用的防护规则（缓存 / 限速 / UA 校验）。

前提：域名的 DNS 已经托管在 Cloudflare，R2 bucket 已经绑好自定义域名，
      更新包放在 https://<你的域名><前缀>/ 下面（例如 /maa/update-v2.2.zip）。

用法（Windows 命令行）：

    set CF_API_TOKEN=你的token
    python tools/setup_cloudflare_rules.py --zone example.com --prefix /maa --dry-run
    python tools/setup_cloudflare_rules.py --zone example.com --prefix /maa

token 权限只要 Zone → Cache Rules / WAF / Zone Rulesets 的「编辑」，
另外 Zone → Zone 的「读取」（用来查 zone id）。

脚本是幂等的：它只增改自己加的那几条（description 以 [MAA] 开头），
不会动你在控制台里手配的其它规则。加 --dry-run 只打印将要提交的内容。
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.cloudflare.com/client/v4"
TAG = "[MAA]"

PHASE_CACHE = "http_request_cache_settings"
PHASE_RATE = "http_ratelimit"
PHASE_WAF = "http_request_firewall_custom"

UA_HINT = "MAAZMXY4-Updater"


def esc(text):
    """把路径塞进 CF 表达式里，需要把反斜杠和双引号转义。"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def cf_request(token, method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit("Cloudflare 返回 %s：%s" % (exc.code, detail))


def zone_id_of(token, zone):
    resp = cf_request(token, "GET", "/zones?name=" + urllib.parse.quote(zone))
    items = resp.get("result") or []
    if not items:
        raise SystemExit("找不到 zone %s（确认域名托管在 Cloudflare）" % zone)
    return items[0]["id"]


def get_entrypoint(token, zid, phase):
    try:
        resp = cf_request(token, "GET", "/zones/%s/rulesets/phases/%s/entrypoint" % (zid, phase))
    except SystemExit:
        return []
    return (resp.get("result") or {}).get("rules") or []


def put_entrypoint(token, zid, phase, rules, dry_run):
    if dry_run:
        print("--- %s 将要提交 %d 条规则 ---" % (phase, len(rules)))
        print(json.dumps({"rules": rules}, ensure_ascii=False, indent=2))
        return
    cf_request(token, "PUT", "/zones/%s/rulesets/phases/%s/entrypoint" % (zid, phase),
               {"rules": rules})
    print("已更新 %s（%d 条）" % (phase, len(rules)))


def merge(old, mine):
    """保留别人配的规则，替换掉之前由本脚本加的同名规则。"""
    kept = [r for r in old if not str(r.get("description", "")).startswith(TAG)]
    return kept + mine


def normalize_prefix(raw):
    """把 --prefix 规范成 '/maa' 这种形式。

    Windows 的 git-bash / MSYS 会把以 / 开头的参数改写成 C:/.../maa，
    这里检测到盘符就只取最后一段，避免把本机路径写进 Cloudflare 表达式。
    """
    p = (raw or "").strip().replace("\\", "/").strip("/")
    if not p:
        return ""
    if ":" in p:
        p = p.rsplit("/", 1)[-1]
    return "/" + p


def build_rules(prefix):
    prefix = normalize_prefix(prefix)
    p = esc(prefix) if prefix else ""
    in_scope = ('starts_with(http.request.uri.path, "%s/")' % p if p
                else '(http.request.uri.path ne "")')
    zip_rule = {
        "action": "set_cache_settings",
        "expression": '(%s and ends_with(http.request.uri.path, ".zip"))' % in_scope,
        "description": TAG + " 更新包缓存 1 天",
        "action_parameters": {
            "cache": True,
            "edge_ttl": {"mode": "override_origin", "default": 86400},
        },
    }
    manifest_rule = {
        "action": "set_cache_settings",
        "expression": '(http.request.uri.path eq "%s/version.json")' % p,
        "description": TAG + " 版本清单缓存 60 秒",
        "action_parameters": {
            "cache": True,
            "edge_ttl": {"mode": "override_origin", "default": 60},
        },
    }
    rate_rule = {
        "action": "block",
        "expression": in_scope,
        "description": TAG + " 单 IP 限速 60 次/分钟",
        "ratelimit": {
            "characteristics": ["ip.src"],
            "period": 60,
            "requests_per_period": 60,
            "mitigation_timeout": 600,
        },
    }
    waf_rule = {
        "action": "managed_challenge",
        "expression": '(%s and not http.user_agent contains "%s" '
                      'and not http.user_agent contains "curl")' % (in_scope, UA_HINT),
        "description": TAG + " 非更新器 UA 走人机验证",
    }
    return [zip_rule, manifest_rule], [rate_rule], [waf_rule]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", required=True, help="托管在 Cloudflare 的域名，如 example.com")
    ap.add_argument("--prefix", default="/maa", help="更新文件所在的路径前缀，默认 /maa")
    ap.add_argument("--token", default=None, help="API Token，默认读环境变量 CF_API_TOKEN")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不提交")
    ap.add_argument("--skip-waf", action="store_true", help="不配置 UA 校验规则")
    args = ap.parse_args()

    token = args.token or os.environ.get("CF_API_TOKEN", "")
    if not token and not args.dry_run:
        raise SystemExit("缺少 token：用 --token 传入，或先 set CF_API_TOKEN=...")
    if not token:
        # dry-run 且没 token：只做本地规则打印，不联网
        cache_rules, rate_rules, waf_rules = build_rules(args.prefix)
        for phase, rules in ((PHASE_CACHE, cache_rules), (PHASE_RATE, rate_rules),
                             (PHASE_WAF, waf_rules)):
            put_entrypoint(None, "DRYRUN", phase, rules, True)
        print("\n（没给 token，只打印规则；正式提交需要 token）")
        return 0

    zid = zone_id_of(token, args.zone)
    print("zone id: %s" % zid)

    cache_rules, rate_rules, waf_rules = build_rules(args.prefix)

    old = get_entrypoint(token, zid, PHASE_CACHE)
    put_entrypoint(token, zid, PHASE_CACHE, merge(old, cache_rules), args.dry_run)

    old = get_entrypoint(token, zid, PHASE_RATE)
    put_entrypoint(token, zid, PHASE_RATE, merge(old, rate_rules), args.dry_run)

    if not args.skip_waf:
        old = get_entrypoint(token, zid, PHASE_WAF)
        put_entrypoint(token, zid, PHASE_WAF, merge(old, waf_rules), args.dry_run)

    if args.dry_run:
        print("\n这是 dry-run，什么都没改。去掉 --dry-run 才会真正提交。")
    else:
        print("\n完成。建议到 Dashboard 里再开一条 $1 预算告警（Notifications → Billing）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
