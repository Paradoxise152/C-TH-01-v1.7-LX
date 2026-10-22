"""导出本次 AI session 记录（供 ai-sessions/ 提交）。

来源：DSH 客户端把会话存成 ``~/.dsh/sessions/<workspace-hash>/session-*/session.v4.jsonl.zstd``。
本脚本用 Python 3.14 的 ``compression.zstd`` 解压，按工作区 ``cwd`` 匹配到本题会话，
脱敏后写出：

- ``ai-sessions/cth01-session-01.jsonl``：完整事件流（原生 JSONL，含工具调用与结果）
- ``ai-sessions/cth01-session-01.md``：同内容的人可读版本（工具结果过长处截断并标注，
  全文以 JSONL 为准）

脱敏：把本机绝对路径中的用户名替换为 ``[REDACTED:USERPROFILE]``，单独的账户名替换为
``[REDACTED:USER]``；不含密钥、令牌或第三方个人信息。导出是只读操作，不修改原始记录。

用法（在项目根目录）：
    python ai-sessions/export_session.py
依赖 Python 3.14+（``compression.zstd``）；本脚本只用于留存证据，不参与评测。
"""
import argparse
import json
import os
import re
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKSPACE = ROOT.parents[1]

RESULT_LIMIT = 6000
SYSTEM_LIMIT = 3000


def redact(text, user_profile, user_name):
    text = text.replace(str(user_profile), "[REDACTED:USERPROFILE]")
    text = re.sub(re.escape(user_name), "[REDACTED:USER]", text, flags=re.IGNORECASE)
    return text


def find_session(explicit):
    if explicit:
        return Path(explicit)
    base = Path(os.path.expanduser("~")) / ".dsh" / "sessions"
    candidates = []
    for path in base.glob("*/*/session.v4.jsonl.zstd"):
        try:
            import compression.zstd as zstd
        except ImportError:  # pragma: no cover
            raise SystemExit("need Python 3.14+ (compression.zstd)")
        raw = zstd.decompress(path.read_bytes())
        head = raw.split(b"\n", 1)[0]
        meta = json.loads(head.decode("utf-8"))
        if str(meta.get("cwd", "")).lower() == str(WORKSPACE).lower():
            candidates.append((meta.get("createdAt", 0), path))
    if not candidates:
        raise SystemExit("no session found for %s" % WORKSPACE)
    candidates.sort()
    return candidates[-1][1]


def norm_newlines(text):
    """工具输出里混有 CRLF/CR；统一成 LF，避免整份记录被 git 判为混合换行。"""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def content_text(parts):
    out = []
    for part in parts or []:
        kind = part.get("type")
        if kind == "text":
            out.append(norm_newlines(part.get("text", "")))
        elif kind == "reasoning":
            out.append("[reasoning]\n" + norm_newlines(part.get("text", "")))
        elif kind == "file":
            att = part.get("attachment", {})
            out.append("[attached file: %s (%s bytes)]" % (att.get("name"), att.get("bytes")))
        elif kind == "tool-call":
            out.append("[tool call] %s %s" % (part.get("name"), norm_newlines(part.get("arguments", ""))))
        else:
            out.append(json.dumps(part, ensure_ascii=False))
    return "\n".join(out)


def render_markdown(rows):
    lines = ["# C-TH-01 AI session（人可读版）", ""]
    lines.append(
        "> 完整事件流见同目录 `cth01-session-01.jsonl`；本文件对超长工具结果做了截断并标注。"
    )
    lines.append("")
    for row in rows:
        kind = row.get("type")
        seq = row.get("seq")
        data = row.get("data") or {}
        if kind == "user/message":
            message = data.get("message", data)
            body = content_text(message.get("content"))
            lines += ["## user (seq %s)" % seq, "", body, ""]
        elif kind == "assistant/message":
            message = data.get("message", {})
            body = content_text(message.get("content"))
            if body.strip():
                lines += ["## assistant (seq %s)" % seq, "", body, ""]
        elif kind == "tool/call":
            lines += [
                "### tool call %s (seq %s)" % (data.get("name"), seq),
                "",
                "```json",
                str(data.get("arguments", "")),
                "```",
                "",
            ]
        elif kind == "tool/result":
            message = data.get("message", {})
            body = content_text(message.get("content"))
            if len(body) > RESULT_LIMIT:
                body = body[:RESULT_LIMIT] + "\n...[truncated, see jsonl]"
            lines += ["### tool result (seq %s)" % seq, "", "```", body, "```", ""]
        elif kind == "system/message":
            message = data.get("message", {})
            body = content_text(message.get("content"))
            if len(body) > SYSTEM_LIMIT:
                body = body[:SYSTEM_LIMIT] + "\n...[truncated, see jsonl]"
            lines += ["## system prompt (seq %s)" % seq, "", "```", body, "```", ""]
        elif kind in ("plan/mode", "approval/policy", "todo/write", "session/title", "model/selection"):
            lines += ["### %s (seq %s)" % (kind, seq), "", "```json",
                      json.dumps(data, ensure_ascii=False, indent=1), "```", ""]
    # 人可读版去掉行尾空白（工具输出常带填充空格）；逐字全文以 JSONL 为准。
    text = "\n".join(lines)
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    return text.rstrip("\n") + "\n"


def main():
    parser = argparse.ArgumentParser(description="export this DSH session record")
    parser.add_argument("--session", help="path to session.v4.jsonl.zstd")
    parser.add_argument("--out", default=str(HERE))
    args = parser.parse_args()
    try:
        import compression.zstd as zstd
    except ImportError:
        raise SystemExit("need Python 3.14+ (compression.zstd)")

    session = find_session(args.session)
    rows = [
        json.loads(line)
        for line in zstd.decompress(session.read_bytes()).decode("utf-8").splitlines()
        if line.strip()
    ]
    user_profile = Path(os.path.expanduser("~"))
    user_name = user_profile.name
    out = Path(args.out)

    lines = []
    for row in rows:
        text = redact(json.dumps(row, ensure_ascii=False), user_profile, user_name)
        lines.append(text)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cth01-session-01.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )

    redacted_rows = [json.loads(line) for line in lines]
    (out / "cth01-session-01.md").write_text(
        render_markdown(redacted_rows), encoding="utf-8", newline="\n"
    )

    counts = {}
    for row in rows:
        counts[row.get("type")] = counts.get(row.get("type"), 0) + 1
    print(json.dumps({
        "source": str(session),
        "events": len(rows),
        "types": counts,
        "out": str(out),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
