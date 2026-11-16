"""Restores neutral file types and the laptop's Acquisition entry (release specification section 15, item 33; source review).

What it changes
  Restores CONFIG, URL and CONSOLE_LOG with the three specified neutral
  descriptions, original icons and non-lab criteria. Open maps to NEdit;
  transform 501 removed the URL-taking browser action D32WebOpen. NEdit's
  accepted data types expand accordingly. If D32WebOpen is present, the URL
  map uses that existing action and NEdit need not accept URL.
  Restores the original Acquisition action, Library subpanel control and
  Application Manager entry with transform 603's Deep32Library names.
  The laptop's existing e26 -acquisitions not-yet behaviour stays.
Usage
  apply(tree_dir): tree_dir is the build tree after transforms 101-806 and 990.
  Returns the changed files.
Exit
  Raises when a target or expected text is missing or its count differs.
  A second run on its output raises, as does an empty folder.
"""
import os

EDITS = {
    'theme/src/theme-data.js': [
        ("  A({ name: 'Deep32LibraryPinpointer', label: 'Pinpointer', icon: 'D32srch', exec: '$D32BIN/e26 -pinpointer', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',\n"
         "      desc: 'Deep32 Library\\'s Pinpointer.' }),\n",
         "  A({ name: 'Deep32LibraryAcquisitions', label: 'Acquisition', icon: 'D32acq', exec: '$D32BIN/e26 -acquisitions', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',\n"
         "      desc: 'Downloads and Send to RAG requests, with their state.' }),\n"
         "  A({ name: 'Deep32LibraryPinpointer', label: 'Pinpointer', icon: 'D32srch', exec: '$D32BIN/e26 -pinpointer', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',\n"
         "      desc: 'Deep32 Library\\'s Pinpointer.' }),\n", 1),
        ('  A({ name: \'D32NEditFile\', label: \'Open in NEdit\', icon: \'D32nedit\', exec: \'/usr/local/bin/nedit "%Arg_1%"\', owner: \'APPS (pkg nedit)\',\n'
         "      argType: 'YANG', desc: 'Open this file in NEdit.' }),\n",
         '  A({ name: \'D32NEditFile\', label: \'Open in NEdit\', icon: \'D32nedit\', exec: \'/usr/local/bin/nedit "%Arg_1%"\', owner: \'APPS (pkg nedit)\',\n'
         "      argType: 'CONFIG,CONSOLE_LOG,URL,YANG', desc: 'Open this file in NEdit.' }),\n", 1),
        ("  { argType: 'YANG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.12' },\n",
         "  { argType: 'URL', to: 'D32NEditFile', file: 'fathom.dt', train: 'D.32.17' },\n"
         "  { argType: 'CONFIG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.16' },\n"
         "  { argType: 'CONSOLE_LOG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.16' },\n"
         "  { argType: 'YANG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.12' },\n", 1),
        ("  { name: 'YANG', file: 'deep32-apps.dt', icon: 'D32yang', actions: 'Open,D32NEditFile', train: 'D.32.12', mime: 'application/yang',\n"
         "    desc: 'A YANG module.', criteria: [{ name: '*.yang', mode: 'f' }] },\n",
         "  { name: 'URL', file: 'fathom.dt', icon: 'D32web', actions: 'Open', train: 'D.32.17',\n"
         "    desc: 'Web link', criteria: [{ name: '*.url', mode: 'f' }] },\n"
         "  { name: 'CONFIG', file: 'deep32-apps.dt', icon: 'D32cfg', actions: 'Open,D32NEditFile', train: 'D.32.16',\n"
         "    desc: 'Device configuration file',\n"
         "    criteria: [{ name: '*.cfg', mode: 'f' }, { name: '*-confg', mode: 'f' }] },\n"
         "  { name: 'CONSOLE_LOG', file: 'deep32-apps.dt', icon: 'D32cfg', actions: 'Open,D32NEditFile', train: 'D.32.16',\n"
         "    desc: 'Console log', criteria: [{ name: '*.session.log', mode: 'f' }] },\n"
         "  { name: 'YANG', file: 'deep32-apps.dt', icon: 'D32yang', actions: 'Open,D32NEditFile', train: 'D.32.12', mime: 'application/yang',\n"
         "    desc: 'A YANG module.', criteria: [{ name: '*.yang', mode: 'f' }] },\n", 1),
        ("      C({ name: 'D32ManCtl', pos: 5, icon: 'D32man', label: 'Manual Pages', push: 'Dtmanpageview' }),\n",
         "      C({ name: 'D32AcqCtl', pos: 3, icon: 'D32acq', alt: 'D32acqN', monitor: 'file', fileName: '$HOME/.d32/e26/requests.changed',\n"
         "          label: 'Acquisition', push: 'Deep32LibraryAcquisitions', train: 'D.32.14' }),\n"
         "      C({ name: 'D32ManCtl', pos: 5, icon: 'D32man', label: 'Manual Pages', push: 'Dtmanpageview' }),\n", 1),
        ("'Deep32LibraryPinpointer',\n"
         "      'D32Status'",
         "'Deep32LibraryPinpointer',\n"
         "      'Deep32LibraryAcquisitions', 'D32Status'", 1),
    ],
}


def apply(tree_dir):
    changed = []
    for rel in sorted(EDITS):
        path = os.path.join(tree_dir, *rel.split("/"))
        if not os.path.isfile(path):
            raise FileNotFoundError("target missing: %s" % rel)
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
        browser = text.count("  A({ name: 'D32WebOpen',")
        if browser not in (0, 1):
            raise ValueError("%s: URL browser action found %d times" % (rel, browser))
        for old, new, count in EDITS[rel]:
            if browser:
                new = new.replace("argType: 'CONFIG,CONSOLE_LOG,URL,YANG'",
                                  "argType: 'CONFIG,CONSOLE_LOG,YANG'")
                new = new.replace("argType: 'URL', to: 'D32NEditFile'",
                                  "argType: 'URL', to: 'D32WebOpen'")
            found = text.count(old)
            if found != count:
                raise ValueError("%s: %r found %d times, expected %d" % (rel, old[:60], found, count))
            text = text.replace(old, new)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        changed.append(rel)
    return changed
