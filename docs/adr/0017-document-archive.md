# 17. A Document travels as an .fsdoc archive and is stored as a folder

Date: 2026-11-17

## Status

Accepted

## Context

A Document is a folder under git ([ADR 0001](0001-document-is-a-folder-under-git.md)). ADR 0001 rejected a single container file as the storage format, because every save would rewrite it and git would lose per-tab history, diff and merge.

A folder cannot be downloaded from the browser client, uploaded to it, or attached to a mail. The browser client (ADR 0013) runs on an iPad, where a person handles files one at a time. The desktop has the same need when a Document goes to someone without a git server.

Alternatives considered:

- **Store the Document as one file.** Rejected by ADR 0001, for the reasons above.
- **Send the git repository** (a bundle or a zipped `.git`). It carries Versions and Workspaces, but a received `.git` is untrusted input that the app must never run git in, and an import is already a `git clone --no-local` of a folder. Unpacking a repository safely from bytes is a larger job than this needs.
- **A zip of the whole folder.** Simple, but it would carry the lock file, autosaves, editor dot-files and anything else in the folder, and a zip taken from a stranger can hold `../` names, symlinks and zip bombs.

## Decision

A Document is stored as a folder and travels as one file, the `.fsdoc` archive: a zip holding a marker (`freeshape-archive.json`, format `freeshape-archive`, version 1), `document.json`, `.gitattributes`, each tab file the manifest names and the blobs beside them. Nothing else goes in. Versions and Workspaces are not carried.

- `freeshape/doc_archive.py` packs and unpacks. It has no Qt and takes and returns bytes and paths, so the server can call it off the event loop.
- Pack reads through the Document store, with the store's checks. The output is deterministic: sorted names, a fixed time and mode.
- Unpack refuses, before it writes anything, an archive with no marker, a name that is absolute, holds `..`, a backslash or a drive, or is not an allowed entry, a symlink entry, more than 5,000 entries, more than 1 GiB unpacked, or a compression ratio above 200.
- Unpack writes a new folder named from the archive's name, gives the Document a fresh id, makes a new repository through gitstore (the step an import of a folder with no repository uses), and registers the folder as one the app created. A Document opened from an archive is a new Document, not the one it was packed from.
- The desktop's Document menu gets Open Document archive… and Save Document archive…. Open unpacks into the managed folder and opens the result. Save saves the Document first, then writes the archive.

## Consequences

- A Document can be downloaded, uploaded and mailed as one file, with no git on either side of the transfer.
- History stays with the folder. A person who wants Versions on the other side must send the folder or push to a git server.
- The fresh id means a Link from another Document that names the original does not follow the copy. Such Links pin a Version, which the archive does not carry, so they could not resolve anyway.
- A tab whose file name the archive cannot hold (a colon, or a segment starting with a dot) is refused on pack with a message naming the tab.
- The limits (5,000 entries, 1 GiB, ratio 200) bound what one archive can hold. A Document over them stays a folder.
