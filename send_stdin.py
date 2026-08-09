import sys, json, io
import claude_drive

raw = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig").read()
cmd = json.loads(raw)
print(claude_drive.send_command(cmd))
