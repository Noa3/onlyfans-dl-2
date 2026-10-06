"""Optional command-line interface using the same engine as the GUI."""
from __future__ import annotations
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import __version__
from .api import ApiClient, load_rules
from .auth import Credentials, SessionError, load_session, parse_session, MAX_IMPORT_BYTES
from .common import AppError, Cancelled, Control
from .downloads import CATEGORIES, Options, parse_profiles, run_downloads
from .settings import DEFAULT_RULES_SOURCE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Local downloader UI. With no arguments, opens the desktop app. Only use your own authorized session/media.')
    parser.add_argument('profiles',nargs='*',help='Creator usernames or profile URLs (CLI mode).')
    parser.add_argument('--version',action='version',version=__version__)
    parser.add_argument('--cli',action='store_true',help='Run without a graphical interface.')
    parser.add_argument('--output',type=Path,default=Path.home()/'Downloads'/'OnlyFans',help='Download folder.')
    parser.add_argument('--all-subscriptions',action='store_true',help='Select all active subscriptions.')
    parser.add_argument('--skip',default='',help='Comma-separated usernames to exclude.')
    dates = parser.add_mutually_exclusive_group()
    dates.add_argument('--days',type=int,default=0,help='Only media from the last N days; 0 means all dates.')
    dates.add_argument('--since',help='Earliest date, YYYY-MM-DD in UTC.')
    parser.add_argument('--content',nargs='+',choices=CATEGORIES,default=list(CATEGORIES))
    parser.add_argument('--types',nargs='+',choices=['photos','videos','audio'],default=['photos','videos','audio'])
    parser.add_argument('--no-albums',action='store_true')
    parser.add_argument('--flat',action='store_true',help='Do not create content-category subfolders.')
    parser.add_argument('--previews',action='store_true',help='Allow a separately named preview when full media is unavailable.')
    parser.add_argument('--scan-only',action='store_true',help='Discover files without downloading media or creating an output folder.')
    parser.add_argument('--rules',default=DEFAULT_RULES_SOURCE,help='Public HTTPS signing-rules source or local JSON file.')
    parser.add_argument('--refresh-rules',action='store_true',help='Refresh public signing rules and exit.')
    parser.add_argument('--test-session',action='store_true',help='Verify the session and exit.')
    parser.add_argument('--session-stdin',action='store_true',help='Read session JSON, headers, or cURL text from stdin (never executed).')
    parser.add_argument('--use-keyring',action='store_true',help='Read the session explicitly saved by the GUI to the OS credential store.')
    return parser


def cli_main(args: argparse.Namespace) -> int:
    control = Control()
    def emit(kind,value):
        if kind in {'log','phase'}:
            print(value,flush=True)
        elif kind == 'overall':
            print(f"Processed {value['done']}/{value['total']} files",flush=True)
    try:
        if args.refresh_rules:
            load_rules(args.rules,control,force=True,emit=emit)
            print('Public signing rules refreshed. Live API compatibility is not implied.')
            return 0
        if args.session_stdin and args.use_keyring:
            raise AppError('Choose either --session-stdin or --use-keyring.')
        if args.session_stdin:
            text = sys.stdin.read(MAX_IMPORT_BYTES+1)
            auth = parse_session(text)
            del text
        elif args.use_keyring:
            auth = load_session()
        else:
            auth = Credentials(os.environ.get('OFDL_USER_ID',''),os.environ.get('OFDL_USER_AGENT',''),
                               os.environ.get('OFDL_X_BC',''),os.environ.get('OFDL_SESS_COOKIE','')).validate()
        if args.days < 0 or args.days > 36500:
            raise AppError('--days must be between 0 and 36500.')
        since = None
        if args.since:
            try:
                since = datetime.strptime(args.since,'%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                raise AppError('--since must be a valid YYYY-MM-DD date.') from None
        elif args.days:
            since = datetime.now(timezone.utc)-timedelta(days=args.days)
        rules = load_rules(args.rules,control,emit=emit)
        with ApiClient(auth,rules,control,emit=emit) as client:
            if args.test_session:
                user = client.user('me')
                print('Session accepted by the account endpoint.')
                return 0
            profiles = parse_profiles(' '.join(args.profiles))
            if args.all_subscriptions or profiles == ['all']:
                profiles = parse_profiles(' '.join(str(row.get('username','')) for row in client.paginate('/subscriptions/subscribes','subscriptions')))
            excluded = set(parse_profiles(args.skip))
            profiles = [name for name in profiles if name not in excluded]
            options = Options(args.output,profiles,tuple(args.content),photos='photos' in args.types,
                              videos='videos' in args.types,audio='audio' in args.types,albums=not args.no_albums,
                              subfolders=not args.flat,previews=args.previews,since=since,dry_run=args.scan_only)
            summary = run_downloads(client,options,control,emit)
        print(f'Downloaded: {summary.downloaded}; existing: {summary.existing}; planned: {summary.planned}; '
              f'filtered: {summary.filtered}; unavailable: {summary.unavailable}; issues: {summary.errors}.')
        return 2 if summary.errors else 0
    except (KeyboardInterrupt,Cancelled):
        control.stop()
        print('Stopped; completed files are kept.',file=sys.stderr)
        return 130
    except (AppError,SessionError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'Operation failed ({type(exc).__name__}). Check permissions, dependencies and disk space. Raw exception details were withheld to protect session data.',file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cli or args.profiles or args.test_session or args.refresh_rules:
        return cli_main(args)
    from .ui import launch
    launch()
    return 0
