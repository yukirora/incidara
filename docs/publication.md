# Publication checklist

Keep the GitHub repository private until every item below is confirmed.

- [ ] The company or code owner has approved publication of the selected source.
- [ ] Contributors to the Console and shared components have approved the chosen license.
- [ ] A `LICENSE` file has been added.
- [ ] `make verify` passes from a clean checkout.
- [ ] GitHub secret scanning reports no findings.
- [ ] Production dependency audits have no unresolved high-severity findings.
- [ ] All credentials discovered in the source repository have been revoked or rotated.
- [ ] Screenshots and examples contain synthetic data only.
- [ ] The repository has no private Git history, runtime data, SSH keys, or internal endpoints.

Changing visibility is a separate, deliberate action:

```bash
gh repo edit --visibility public
```

Do not run that command until approval and licensing are documented.
