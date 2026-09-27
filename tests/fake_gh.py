"""Stand-in for the GitHub CLI in tests: `gh pr list --repo OWNER/NAME ...` answers from a JSON file.

FAKE_GH_PRS names a JSON object mapping "OWNER/NAME" to the list `gh pr list --json` would print, or to
{"fail": TEXT} to exit 1 with TEXT on stderr. Each call's arguments are appended to FAKE_GH_LOG as one JSON line.
"""
import json
import os
import sys

args = sys.argv[1:]
if os.environ.get("FAKE_GH_LOG"):
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(args, ensure_ascii=False) + "\n")
if args[:2] != ["pr", "list"] or "--repo" not in args:
    sys.stderr.write("fake gh answers only `pr list --repo`\n")
    sys.exit(2)
with open(os.environ["FAKE_GH_PRS"], encoding="utf-8") as answers:
    answer = json.load(answers).get(args[args.index("--repo") + 1], [])
if isinstance(answer, dict):
    sys.stderr.write(answer["fail"] + "\n")
    sys.exit(1)
sys.stdout.buffer.write(json.dumps(answer, ensure_ascii=False).encode("utf-8"))
