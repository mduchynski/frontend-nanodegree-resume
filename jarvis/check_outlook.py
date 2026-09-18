#!/usr/bin/env python3
"""Show what Jarvis actually sees in Outlook.

Run this when mail results look wrong:

    .venv\\Scripts\\activate
    python check_outlook.py

It lists the mailboxes in your profile, the inbox Jarvis is reading, the
newest messages Outlook reports, and the same query Jarvis runs -- so a
mismatch between the two is immediately visible.
"""
from __future__ import annotations

import sys


def main() -> int:
    if sys.platform != "win32":
        print("This only means anything on Windows.")
        return 1

    try:
        import pythoncom
        import win32com.client
    except ImportError:
        print("pywin32 is not installed. Run:  pip install -r requirements.txt")
        return 1

    from tools.outlook_local import OBJ_MAIL, build_mail_filter, inbox

    pythoncom.CoInitialize()
    try:
        try:
            app = win32com.client.Dispatch("Outlook.Application")
        except Exception as exc:  # noqa: BLE001
            print(f"Could not reach Outlook: {exc}")
            print("Is the classic Outlook desktop app running? 'New Outlook' cannot be automated.")
            return 1

        ns = app.GetNamespace("MAPI")

        print("=" * 66)
        print("  MAILBOXES IN YOUR PROFILE")
        print("=" * 66)
        for store in ns.Stores:
            try:
                print(f"  - {store.DisplayName}")
            except Exception:  # noqa: BLE001
                print("  - (unreadable store)")
        print("\n  Set OUTLOOK_ACCOUNT in .env to read one other than the default.")

        folder = inbox(ns)
        print("\n" + "=" * 66)
        print("  THE INBOX JARVIS READS")
        print("=" * 66)
        print(f"  Folder : {folder.FolderPath}")
        print(f"  Store  : {folder.Store.DisplayName}")
        print(f"  Items  : {folder.Items.Count}")
        print(f"  Unread : {folder.UnReadItemCount}")

        subfolders = [f.Name for f in folder.Folders]
        if subfolders:
            print("\n  Subfolders (Jarvis does NOT read these -- a rule that files")
            print(f"  incoming mail into one will hide it): {', '.join(subfolders[:12])}")

        print("\n" + "=" * 66)
        print("  NEWEST 10, SORTED BY OUTLOOK")
        print("=" * 66)
        items = folder.Items
        items.Sort("[ReceivedTime]", True)
        shown = 0
        for item in items:
            try:
                if getattr(item, "Class", OBJ_MAIL) != OBJ_MAIL:
                    continue
                flag = "UNREAD" if item.UnRead else "      "
                print(f"  {flag}  {str(item.ReceivedTime)[:16]}  {str(item.Subject)[:46]}")
            except Exception:  # noqa: BLE001
                continue
            shown += 1
            if shown >= 10:
                break
        if not shown:
            print("  (nothing readable in the inbox)")

        print("\n" + "=" * 66)
        print("  WHAT JARVIS'S OWN QUERY RETURNS")
        print("=" * 66)
        for label, query, unread in [
            ("most recent", None, False),
            ("unread only", None, True),
        ]:
            restriction = build_mail_filter(query, unread)
            found = folder.Items
            if restriction:
                found = found.Restrict(restriction)
            found.Sort("[ReceivedTime]", True)
            print(f"\n  {label}  (filter: {restriction or 'none'})")
            count = 0
            for item in found:
                try:
                    if getattr(item, "Class", OBJ_MAIL) != OBJ_MAIL:
                        continue
                    print(f"      {str(item.ReceivedTime)[:16]}  {str(item.Subject)[:44]}")
                except Exception:  # noqa: BLE001
                    continue
                count += 1
                if count >= 5:
                    break
            if not count:
                print("      (no results)")

        print("\n" + "=" * 66)
        print("  If the two lists disagree, copy this output into the chat.")
        print("=" * 66)
        return 0
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
