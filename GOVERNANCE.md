# Governance

Deep32 is led by one person. This page says who decides what, how changes are reviewed, and how the project keeps
going if I am unavailable.

## Roles

| Role | Who | Rights | Duties |
|---|---|---|---|
| Project lead | Jeffrey A. Cummings ([@hellobpdu](https://github.com/hellobpdu)) | Admin on the repository and every decision: direction, releases, licensing, security, governance and maintainers | Everything not listed for another role, including releases, release signing and security reports |
| Co-maintainer | *(none yet)* | Write access to the repository | One: before each release, review at least half of the changes in it that someone else wrote |
| Contributor | Anyone | Opens issues and pull requests | Follows [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md); signs off commits (DCO) |

The project lead adds and removes co-maintainers at any time, by a pull request that changes this table.

## How decisions are made

Anyone can propose a change in an issue or a pull request. The project lead decides. The
[roadmap](docs/ROADMAP.md) records the direction and what Deep32 will not do.

## Reviews

From 0.1.0-alpha.3 on, changes reach the main branch through pull requests. Once the project has a co-maintainer,
at least half of the changes in each release are reviewed before the release by someone other than their author;
until then I review every change myself. A review checks that the change is worthwhile, that the tests and
checks pass, and that it brings in no known security problem. A co-maintainer may review the changes of a release in
one pass.

## Continuity

If I am unable to continue the project, it can still go on. My GitHub account names a successor who can take over
the repository, and my release signing key is kept in a sealed backup that is released to that person only if I am
confirmed unable to continue, so releases stay verifiable with the key the images already trust. Within a week of
that confirmation, the project can create and close issues, accept changes and release new versions.

## Changes to this document

Changes to governance need the project lead's approval.
