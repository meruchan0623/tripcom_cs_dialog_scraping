#!/usr/bin/env python3
"""
Ctrip IM JSON Batch Scanner — Data Extraction Engine
=====================================================
Pure data extraction from Ctrip IM conversation archives.
Outputs structured JSON. Zero business logic baked in.

Core capabilities:
  1. Load & parse IMChatlogExport / IM_Archive JSON files (batch)
  2. Extract messages with role, time, text, sequence
  3. Extract order cards embedded in rawHtml
  4. Filter by role / keyword / time range
  5. Output context windows around matched messages
  6. Basic statistics (counts by dimension)

Design principle:
  This script ONLY extracts and structures raw data.
  All interpretation / classification logic lives upstream (in the AI agent).

Usage:
    python scan_im.py <dir>                          # Summary stats
    python scan_im.py <dir> -o out.json              # Full export as JSON
    python scan_im.py <dir> --role buyer             # Customer messages only
    python scan_im.py <dir> --keyword "refund"       # Text search
    python scan_im.py <dir> --after 2026-04-20       # Date filter
    python scan_im.py <dir> --extract orders         # Pull order cards
    python scan_im.py <dir> --keyword "thanks" --ctx 3   # Match + 3 lines context
    python scan_im.py <dir> --seq-diff              # Show buyer→seller gaps per session
"""

import json
import os
import sys
import re
import glob
import argparse
from datetime import datetime
from collections import Counter, defaultdict

# ─── Order card regex patterns ──────────────────────────────
ORDER_RE = {
    'order_id':     re.compile(r'订单ID：</span><span[^>]*>(\d+)</span>'),
    'product_name': re.compile(r'产品名称：</span><span[^>]*>([^<]+)</span>'),
    'use_date':     re.compile(r'使用日期：</span><span[^>]*>([^<]+)</span>'),
    'amount':       re.compile(r'订单总额：</span><span[^>]*>([^<]+)</span>'),
    'channel':      re.compile(r'来源渠道：</span><span[^>]*>([^<]+)</span>'),
}


def load_session(filepath):
    """Load a single exported IM JSON file. Returns dict or None."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def extract_order(raw_html):
    """Extract order fields from rawHtml string. Returns dict or None."""
    if not raw_html:
        return None
    result = {}
    for key, pat in ORDER_RE.items():
        m = pat.search(raw_html)
        if m:
            result[key] = m.group(1).strip()
    return result or None


def load_all_sessions(dir_path):
    """Load all JSON files from directory. Returns list of session dicts."""
    files = sorted(glob.glob(os.path.join(dir_path, '**', '*.json'), recursive=True))
    sessions = []
    for fp in files:
        data = load_session(fp)
        if data is None:
            continue
        msgs = []
        for m in data.get('messages', []):
            entry = {
                'sender_role': m.get('senderRole', ''),
                'sender_name': m.get('senderName', ''),
                'msg_type': m.get('messageType', ''),
                'text': m.get('text', '') or '',
                'sequence': m.get('sequence', 0),
                'timestamp': m.get('timestampText', ''),
                'has_attachments': bool(m.get('attachments')),
                'order_card': extract_order(m.get('rawHtml', '')),
            }
            msgs.append(entry)
        sessions.append({
            'session_id': data.get('sessionId', ''),
            'cs_name': data.get('csName', ''),
            'title': data.get('title', ''),
            'detail_url': data.get('detailUrl', ''),
            'exported_at': data.get('exportedAt', ''),
            'filename': os.path.basename(fp),
            'message_count': len(msgs),
            'messages': msgs,
        })
    return sessions


def apply_filters(sessions, args):
    """Return filtered copy of sessions based on CLI args."""
    out = []
    for sess in sessions:
        filtered_msgs = []
        for m in sess['messages']:
            # Role filter
            if args.role and m['sender_role'] != args.role:
                continue
            # Keyword filter
            if args.keyword:
                combined = f"{m['text']} ".lower()
                # Also search order card text
                if m['order_card']:
                    combined += ' '.join(str(v) for v in m['order_card'].values()).lower()
                if args.keyword.lower() not in combined:
                    continue
            # Time filter — after
            if m['timestamp']:
                try:
                    dt = datetime.strptime(m['timestamp'], '%Y-%m-%d %H:%M:%S')
                    if args.after and dt < datetime.strptime(args.after, '%Y-%m-%d'):
                        continue
                    if args.before:
                        before_dt = datetime.strptime(args.before, '%Y-%m-%d')
                        before_end = before_dt.replace(hour=23, minute=59, second=59)
                        if dt > before_end:
                            continue
                except ValueError:
                    pass

            filtered_msgs.append(m)
        if not filtered_msgs and (args.role or args.keyword):
            continue  # skip empty sessions when filtering
        out.append({**sess, 'messages': filtered_msgs})
    return out


def build_context_windows(sessions, ctx_radius):
    """Wrap each message with its neighbors as context."""
    out = []
    for sess in sessions:
        msgs = sess['messages']
        for i, m in enumerate(msgs):
            start = max(0, i - ctx_radius)
            end = min(len(msgs), i + ctx_radius + 1)
            context = msgs[start:end]
            out.append({
                'session_id': sess['session_id'],
                'filename': sess['filename'],
                'matched_index': i,
                'context_before': [x for x in context[:i - start]],
                'target_message': m,
                'context_after': [x for x in context[i - start + 1:]],
            })
    return out


def compute_seq_gaps(sessions):
    """Compute inter-message timing gaps within each session.
    Returns list of {session_id, gaps: [{from_seq, to_seq, from_role, to_role, gap_seconds, gap_text}]}"""
    results = []
    for sess in sessions:
        gaps = []
        msgs = sorted(sess['messages'], key=lambda x: x['sequence'])
        for j in range(1, len(msgs)):
            prev, curr = msgs[j - 1], msgs[j]
            if not prev['timestamp'] or not curr['timestamp']:
                continue
            try:
                t_prev = datetime.strptime(prev['timestamp'], '%Y-%m-%d %H:%M:%S')
                t_curr = datetime.strptime(curr['timestamp'], '%Y-%m-%d %H:%M:%S')
                sec = int((t_curr - t_prev).total_seconds())
            except ValueError:
                continue
            gaps.append({
                'from_seq': prev['sequence'],
                'to_seq': curr['sequence'],
                'from_role': prev['sender_role'],
                'to_role': curr['sender_role'],
                'from_time': prev['timestamp'],
                'to_time': curr['timestamp'],
                'gap_seconds': sec,
                'gap_text': format_duration(sec),
            })
        results.append({'session_id': sess['session_id'], 'gaps': gaps})
    return results


def compute_stats(sessions):
    """Compute basic counts across all loaded sessions."""
    total_msgs = sum(s['message_count'] for s in sessions)
    role_counts = Counter()
    type_counts = Counter()
    ts_earliest = None
    ts_latest = None
    lang_counter = Counter()  # rough language detection
    msg_len_total = 0
    msg_len_count = 0
    sessions_with_orders = 0
    total_amount = 0.0

    for s in sessions:
        for m in s['messages']:
            role_counts[m['sender_role']] += 1
            type_counts[m['msg_type']] += 1
            ts = m['timestamp']
            if ts:
                try:
                    dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
                    if ts_earliest is None or dt < ts_earliest:
                        ts_earliest = dt
                    if ts_latest is None or dt > ts_latest:
                        ts_latest = dt
                except ValueError:
                    pass
            txt = m['text']
            if txt:
                msg_len_total += len(txt)
                msg_len_count += 1
                # Rough language detection via character set
                if re.search(r'[\u4e00-\u9fff]', txt):
                    lang_counter['zh'] += 1
                elif re.search(r'[\u0e00-\u0e7f]', txt):  # Thai
                    lang_counter['th'] += 1
                elif re.search(r'[\u0400-\u04ff]', txt):  # Cyrillic/Russian
                    lang_counter['ru'] += 1
                elif re.search(r'[\u00c0-\u024f\u0100-\u017f]', txt):  # Latin extended
                    lang_counter['latin'] += 1
                else:
                    lang_counter['en'] += 1
            if m['order_card']:
                sessions_with_orders += 1
                amt = m['order_card'].get('amount')
                if amt:
                    try:
                        total_amount += float(amt)
                    except ValueError:
                        pass

    avg_msg_len = round(msg_len_total / max(msg_len_count, 1), 1)

    return {
        'sessions': len(sessions),
        'total_messages': total_msgs,
        'by_role': dict(role_counts.most_common()),
        'by_type': dict(type_counts.most_common()),
        'time_earliest': ts_earliest.strftime('%Y-%m-%d %H:%M:%S') if ts_earliest else None,
        'time_latest': ts_latest.strftime('%Y-%m-%d %H:%M:%S') if ts_latest else None,
        'avg_message_length_chars': avg_msg_len,
        'language_distribution': dict(lang_counter.most_common()),
        'sessions_with_order_cards': sessions_with_orders,
        'total_order_amount': round(total_amount, 2) if total_amount else None,
    }


def format_duration(seconds):
    """Human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m:02d}m"


def print_human_summary(stats, sessions):
    """Print summary table to stdout."""
    w = 60
    print("=" * w)
    print("Ctrip IM Archive — Scan Summary")
    print("=" * w)
    print(f"Sessions scanned:     {stats['sessions']}")
    print(f"Total messages:       {stats['total_messages']}")

    br = stats['by_role']
    if br:
        print("\n--- By Sender Role ---")
        for r, c in sorted(br.items()):
            label = {'buyer': 'Customer', 'seller': 'Agent (Jeffery)', 'system': 'System'}.get(r, r)
            pct = c / max(stats['total_messages'], 1) * 100
            print(f"  {label:>20s}: {c:>6} ({pct:.1f}%)")

    ld = stats['language_distribution']
    if ld:
        print("\n--- Language Distribution (rough) ---")
        labels = {'zh': 'Chinese', 'en': 'English', 'th': 'Thai', 'ru': 'Cyrillic', 'latin': 'Latin/Euro'}
        for l, c in sorted(ld.items(), key=lambda x: -x[1]):
            name = labels.get(l, l)
            print(f"  {name:>12s}: {c}")

    if stats['time_earliest']:
        print(f"\n--- Time Range ---")
        print(f"  From: {stats['time_earliest']}")
        print(f"  To:   {stats['time_latest']}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description='Ctrip IM JSON — Pure Data Extraction Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Output formats:
  Default          Human-readable summary table to stdout
  -o file.json     Full structured JSON export (all data)

Examples:
  %(prog)s ./chat_logs                           Summary stats
  %(prog)s ./chat_logs -o full.json              Export everything
  %(prog)s ./chat_logs --role seller             Agent messages only
  %(prog)s "./dir path" --keyword refund         Search text
  %(prog)s ./chat_logs --after 2026-04-20        From date onward
  %(prog)s ./chat_logs --extract orders          Pull order cards
  %(prog)s ./chat_logs -k thanks --ctx 2         Match + 2-msg context window
  %(prog)s ./chat_logs --seq-diff               Timing gaps between messages
""",
    )
    parser.add_argument('directory', help='Directory containing IM_Archive_*.json files')
    parser.add_argument('-o', '--output', help='Write structured JSON output to file')
    parser.add_argument('--role', help='Filter messages by senderRole (buyer/seller/system)')
    parser.add_argument('--keyword', '-k', help='Case-insensitive substring search in message text + order cards')
    parser.add_argument('--after', help='Include messages >= YYYY-MM-DD')
    parser.add_argument('--before', help='Include messages <= YYYY-MM-DD')
    parser.add_argument('--extract', choices=['orders'],
                        help='Extract embedded info (currently: orders from HTML)')
    parser.add_argument('--ctx', type=int, default=0, metavar='N',
                        help='Add N messages before/after each match as context window')
    parser.add_argument('--seq-diff', action='store_true',
                        help='Compute timing gaps between consecutive messages')

    args = parser.parse_args()

    # Step 1: Load all sessions
    sessions = load_all_sessions(args.directory)
    print(f"[LOAD] {len(sessions)} sessions, "
          f"{sum(s['message_count'] for s in sessions)} total messages", file=sys.stderr)

    # Step 2: Apply filters
    filtered = apply_filters(sessions, args) if (args.role or args.keyword or args.after or args.before) else sessions
    filt_count = sum(len(s['messages']) for s in filtered)
    if args.role or args.keyword or args.after or args.before:
        print(f"[FILTER] {filt_count} messages after filtering", file=sys.stderr)

    # Step 3: Build output structure
    stats = compute_stats(sessions)
    output = {'statistics': stats}

    if args.ctx and args.keyword:
        # Context mode: wrap matches with neighbors
        output['context_matches'] = build_context_windows(filtered, args.ctx)
        print(f"[CTX] {len(output['context_matches'])} matches with ±{args.ctx} context", file=sys.stderr)

    if args.seq_diff:
        # Sequence gap mode
        output['timing_gaps'] = compute_seq_gaps(filtered if (args.role or args.keyword or args.after or args.before) else sessions)
        all_gaps = sum(len(g['gaps']) for g in output['timing_gaps'])
        print(f"[GAPS] {all_gaps} inter-message timing gaps computed", file=sys.stderr)

    # Step 4: Output
    if args.output:
        # Full export mode
        export_sessions = filtered if (args.role or args.keyword or args.after or args.before) else sessions
        output['sessions'] = export_sessions
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"[OUT] Written to {args.output}", file=sys.stderr)
    elif args.ctx or args.seq_diff:
        # Structured output for programmatic use → stdout as JSON
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    else:
        # Human-readable summary
        display_sessions = filtered if (args.role or args.keyword or args.after or args.before) else sessions
        print_human_summary(stats, display_sessions)

        # Print individual messages if filtered
        if args.role or args.keyword:
            for s in display_sessions:
                if not s['messages']:
                    continue
                hdr = f"\n[{s['session_id']}] ({s['filename']})"
                try:
                    print(hdr)
                    print("-" * min(len(hdr), 60))
                except UnicodeEncodeError:
                    safe_hdr = hdr.encode('ascii', 'replace').decode('ascii')
                    print(safe_hdr)
                    print("-" * min(len(hdr), 60))
                for m in s['messages']:
                    tag = {'buyer': '[客户]', 'seller': '[客服]', 'system': '[系统]'}.get(m['sender_role'], f"[{m['sender_role']}]")
                    line = f"  {m['timestamp']} | {tag:>6} | seq:{m['sequence']:>3}"
                    oc = ''
                    if m['order_card']:
                        oc = f" | ORDER:{m['order_card'].get('order_id','?')} {m['order_card'].get('product_name','')[:40]}"
                    try:
                        print(line + oc)
                    except UnicodeEncodeError:
                        print((line + oc).encode('ascii', 'replace').decode('ascii'))
                    t = m['text']
                    if t.strip():
                        preview = t[:200] + ('...' if len(t) > 200 else '')
                        # Safe output: replace non-encodable chars
                        try:
                            print(f"           {preview}")
                        except UnicodeEncodeError:
                            print(f"           {preview.encode('ascii', 'replace').decode('ascii')}")


if __name__ == '__main__':
    main()
