# Test fixtures: sources, redactions and licences

Every fixture is real data saved from a public source (tests never call live
APIs), except where marked "constructed". Personal details are removed or
replaced with `REDACTED`, keeping the keys so stripping can be tested.

| Folder | Source | Fetched | Redacted | Licence |
|---|---|---|---|---|
| `retraction_watch/` | Retraction Watch database, distributed by Crossref (gitlab.com/crossref/retraction-watch-data) | 2026-10-18 | `Author`, `Institution` | **No licence required.** Crossref, which owns the database: "Like the rest of our metadata, the retractions are freely available" and "Crossref metadata is freely available to reuse without a license"; it asks that published work using the data cite the source (crossref.org/blog/retraction-watch-retractions-now-in-the-crossref-api, checked 2026-10-29). The data repository names no licence. Cite as: Retraction Watch Database, Crossref (gitlab.com/crossref/retraction-watch-data). |
| `clinicaltrials/` | ClinicalTrials.gov API v2, studies NCT05711641 and NCT02016378 | 2026-10-18 | every value under the ADR-018/ADR-025 drop paths | US government registry data; check clinicaltrials.gov terms of use before redistributing beyond this repository. |
| `europepmc/` | Europe PMC REST and Annotations APIs: search metadata and the `<ref-list>` only, for PMC11662519 (CC BY-NC-ND), PMC11667321 (CC BY-NC), PMC11634091 (CC BY-ND) | 2026-10-24 | author affiliations, author IDs; article body not kept | Bibliographic metadata and citations; each article's own licence as listed. |
| `lookups/` | Crossref, OpenAlex and doi.org responses for DOI 10.1093/aje/kwj325 and test DOIs | 2026-10-25 | ORCIDs, affiliations; **abstracts removed** (publisher copyright) | Bibliographic metadata only. Crossref treats it as facts with no reuse restriction, abstracts excepted; OpenAlex metadata is CC0 (help.openalex.org/data/licenses). |
| `references/` | 50 reference strings from corpus papers' bibliographies, with hand-written expected fields | 2026-10-25 | none needed (citations) | Citations from the articles above and other corpus papers. |
| `c01/`, `c02/` | Stored references and resolution trails from the corpus, with hand-written labels | 2026-10-25 | inherits the above | as above |
| `set/` | The §9.9 fixture set: 35 papers (34 from corpus `pmc-rct-2015-2024`, plus PMC7052980 from corpus `fixtures-in-press`, chosen as a real "retracted while in press" citation) and 11 ClinicalTrials.gov trials. Each file is the exact check input frozen from the database by `set/freeze.py`, plus hand-written `expected` findings and `notes` | 2026-10-27 | no author or affiliation of the citing paper is stored; trials keep only identifiers, status, oversight, design, outcomes and site countries (facilities and cities dropped) | Citations and bibliographic metadata as above; registry data as above |

Constructed data: the C02 "retracted while in press" band and its edge rules,
C05 branch cases, and C01 rule cases are built in the test files themselves,
because the corpus has no real example of them. Each such test says so.
