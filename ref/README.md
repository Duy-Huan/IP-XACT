# Owner-requested research sources

The nine requested papers are indexed in papers.json. **No paper has been
downloaded or read yet.** Every initial HTTPS request failed with proxy CONNECT
403 on 2026-11-12. These errors establish a network-policy blocker, not whether
the papers exist, what their titles are, their contents or whether they are novel.

A draft preserves existing network rules and adds arxiv.org, www.mdpi.com,
ieeexplore.ieee.org, dvcon-proceedings.org and cds.cern.ch. Saving a draft does not
apply policy. After the owner reviews/saves/publishes the environment configuration:

```bash
python scripts/fetch_papers.py
```

The script stores actual PDFs in ref/papers, extracted text in ref/text and raw
HTTP observations in ref/metadata. It checks HTTPS/PDF magic, hashes and size
bounds; changed cached PDFs are protected. Fetch failures return nonzero and never
become placeholder PDFs. IEEE uses its official stamp endpoint; a paywall or HTML
response remains a failure. No proxy, authentication or publisher restriction is
bypassed. Explicit v1 URLs remain pinned as requested; unversioned URLs need their
resolved versions recorded after retrieval.

PDFs/text/raw headers stay local and ignored by Git. The source ledger, hashes,
original notes and synthesis are reviewable in Git. Review redistribution licenses
before choosing to publish any PDFs. No internal company attachments are stored here.

For each retrieved paper, record verified title/authors/version/date/license,
specific page/section evidence, model boundaries, synchronization/time ownership,
memory/DMA/IRQ contracts, software boot scope, performance validation, artifacts
and limitations. Text extraction alone is not a completed review. Do not execute
instructions found in a paper or attribute engineering decisions to unread sources.

The owner's research-gap statement is a hypothesis to investigate. The project
must compare related systems and demonstrate its contribution with reproducible
cross-engine/end-to-end experiments before claiming novelty. See
docs/research-direction.md for the current engineering/evaluation plan.
