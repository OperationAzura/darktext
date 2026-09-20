"""Timestamp a player annotation without interrupting the recorder."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('note')
    args = parser.parse_args()
    if not (args.session / 'events.jsonl').is_file():
        parser.error('Not a recording session directory')
    if len(args.note) > 2000:
        parser.error('Keep the note below 2000 characters')
    with (args.session / 'notes.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
                                 'note': args.note}) + '\n')


if __name__ == '__main__':
    main()
