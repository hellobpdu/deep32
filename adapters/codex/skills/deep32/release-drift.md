# Release drift

Line pointers here (`file:line`) are search seeds from the corpus this file was written against, and some may have
moved by a few lines in a later knowledge base. Find the passage again and take every `local` from `verify_quote`,
never from this file.

## The pins (a setting of this install)

Change a value here to move this install's default; a release the user names in a question always wins.

| Component | Default release pin |
|---|---|
| C8000V and Catalyst 9300 | IOS XE **17.9** |
| SD-WAN Manager, Validator, Controller and edges | **20.9**, with IOS XE Catalyst SD-WAN 17.9 on the edges |
| Catalyst Center (DNA Center) | **2.3** |
| ISE | **3.1** (configuration questions; ISE automation follows collections.md) |
| IOSv and IOSv-L2 | 15.8 and 15.2 |

The rest of these files write the pins as "17.9", "20.9", "2.3" and "3.1": read the values in this table instead
when they differ.

## What the corpus covers

The Release and Kind of every document come from `list_documents` and each hit's `release`. As written:

- **17.9-pinned books.** Prefer them whenever the topic is covered:
  - the Catalyst 9300 17.9 books (`c9300-179-*`);
  - the LISP/VXLAN fabric book (`c9k-179-lisp-vxlan`);
  - the 17.9 programmability books (`iosxe-179-*`);
  - SD-WAN 20.9 API (`sdwan-209-api`).
- **Catalyst 9300/9500 books and the C9300 command reference: 17.18.x.** These are nine releases newer than the
  17.9 pin. They mark release-specific features in "Feature History" tables.
- **Router books "IOS XE 17.x"** (IP Routing, MPLS, Security VPN, QoS and others) and the **IOS XE 17 command
  references** (`iosxe-cr-*`) are rolling books. One book mixes features introduced from 17.2 to 17.18, so the
  document's release label proves nothing: check the feature.
- **Catalyst Center guides: 2.3.7.x.** The pin says only "2.3", so a 2.3.7 feature may be missing from an earlier
  2.3 build.
- **SD-WAN guides** are rolling 17.x/20.x books with per-feature release rows. Release 20.9.1 is the most frequently
  named release in them.
- **TechNotes** (`cisco-tn-*`) mostly carry no release. They are the best source for troubleshooting sequences and
  sample output. Confirm the commands against a 17.9 or rolling book.
- **IOS XE 26.x.** The Catalyst 9300 IOS XE 26.x guides (`c9300-26x-*`: inthw, ip, lyr2, nmgmt, qos, rtng, sec,
  sysmgmt, vlan) and `iosxe-26x-prog` sit beside the 17.x books, and SD-WAN Manager API 26.1 (`sdwan-manager-api`)
  beside `sdwan-209-api`. For the 17.9 pin, prefer in this order:
  1. a 17.9 book: `c9300-179-*` (cr, cts, evpn-vxlan-core, ipmcast, nmgmt-fnf, rtng, sec, stack-mgmt),
     `c9400-179-rtng-vrf-lite`, `c9k-179-lisp-vxlan`, `iosxe-179-*` (programmability, and the 17.9 system-message
     guides `iosxe-179-system-messages` and `iosxe-179-sysmsg-adj`), or an `also in` copy at 17.9
     (`${CODEX_HOME}/skills/deep32/deep-engine.md` section 8);
  2. the rolling 17.x router book or command reference (`iosxe17-*`, `iosxe-cr-*`), for router behaviour: a router
     book is not C9300 evidence (Platform drift, below);
  3. 17.18.x (`c9300-1718-*`);
  4. 26.x (`c9300-26x-*`, `iosxe-26x-prog`).
- The C9300 Layer 2, VLAN, QoS, interface and hardware, system-management and IP-services guides exist only as
  17.18.x and 26.x. Cite them with the release in the claim ("C9300 17.18 guide; 17.9 not confirmed"), and confirm
  syntax in `c9300-179-cr`. A statement found only in a 26.x guide is "not shown for 17.9". Never apply 26.x-only
  behaviour, such as newer secure-by-default settings, to the 17.9 pin.
- `release_max` (a search filter) removes a whole document whose release is later than the cap: on the same major
  line (17.18 after 17.9), or numbered by year against a cap numbered before (26.x after 17.9, 20.9 or 3.1). The
  rolling "17.x" books and documents with no release always pass. So `release_max: "17.9"` hides 17.12, 17.18 and
  every 26.x document: it narrows and never decides (`${CODEX_HOME}/skills/deep32/deep-engine.md` section 1, step 2). Skip
  it where no 17.9 book covers the area, and keep the same queries unfiltered beside a filtered group.
- **ISE pin.** For ISE configuration questions prefer `ise31-*` at the 3.1 pin. The ISE 3.4 guides (`ise34-*`) are
  newer than that pin, and the ISE API documents (`ise-api-*`, `ise-pxgrid2-api`, `ise-dataconnect`) carry no
  release. Label a passage from them "ISE 3.4" or "release not stated", and a 3.4-only behaviour "not shown for
  3.1". `release_max: "17.9"` lets ISE 3.4 through (another major line); on an ISE group the filter is
  `release_max: "3.1"`, beside the same queries unfiltered. ISE automation (the ISE REST APIs,
  `${CODEX_HOME}/skills/deep32/collections.md`) follows the user's own release.

## Deciding whether a statement holds on 17.9

1. Find the feature's release. It can be in a "Feature History for ..." table (`Release | Feature | Feature
   Information`, 30 of them in c9300-1718-rtng alone), a "Feature Information for ..." line (over 1,100 in the IP
   Routing book), a command's "Command History" row, or a phrase like "From Cisco IOS XE Release 17.2.1, ...".
   Search near the section you are reading with `grep_corpus`, or use `find_command`, which returns a release.
2. Translate IOS XE train names. These were checked against the names that occur in the corpus.

| Train | Releases |
|---|---|
| Denali | 16.1-16.3 |
| Everest | 16.4-16.6 |
| Fuji | 16.7-16.9 |
| Gibraltar | 16.10-16.12 |
| Amsterdam | 17.1-17.3 |
| Bengaluru | 17.4-17.6 |
| **Cupertino** | **17.7-17.9 (the default pin)** |
| Dublin | 17.10-17.12 |
| (numbered only) | 17.13 and later, for example "Cisco IOS XE 17.13.1" |

3. Decide:
   - Introduced in 17.9.x or earlier: it applies at the pin, if the platform supports it.
   - Introduced after 17.9: write "not available at the 17.9 pin" in the answer.
   - No release found: put "release not stated" in Gaps.
4. SD-WAN pairs controller 20.x with edge IOS XE 17.x. The docs name the pair "Cisco IOS XE Catalyst SD-WAN
   Release 17.9.1a and Cisco vManage Release 20.9.1" (sdwan17-cloudonramp.txt:6857). A feature listed for a later
   pair, 17.10/20.10 or newer, is not at the 20.9 pin.

## Examples found in the corpus

- **Available at the pin:** "From Cisco IOS XE Release 17.2.1, you can modify this default behavior" of EBGP route
  propagation without policies, with `bgp safe-ebgp-policy` (iosxe17-rtr-iprouting-cg.txt:33067).
- **Self-contradiction inside one book:** in the C9300 17.18 command reference, the usage guidelines for
  `export-protocol netflow-v9` say "The device does not support NetFlow v5 export format"
  (c9300-1718-cr.txt:50564). The next entry documents `export-protocol netflow-v5`
  (c9300-1718-cr.txt:50572). Quote both, mark the claim **disputed**, and propose a lab check.
- These were also flagged as release-dependent. They were **not re-verified here**, so find the passages before
  relying on them:
  - when an OSPF router-id change takes effect (17.13.1a);
  - SSH version 1.99 support (17.10);
  - NetFlow v5 on the C9300 (17.12.1);
  - ND Inspection deprecation (17.1.1).

## Platform drift

A C9300 (switch) book is not proof of C8000V (router) behaviour, or the reverse. Use each document's platform (from
`list_documents` and the hit's metadata), and name the platform in the answer whenever you cite a platform-specific
book.
