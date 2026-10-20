# Production release

Argo CD syncs this directory into the `serving` namespace ([`../argocd/ticket-router.yaml`](../argocd/ticket-router.yaml)).
It holds no manifests until the first promotion, so nothing is deployed from it yet.

Do not edit files here by hand. A promotion writes them and opens a pull request:

```bash
smlops promotion-request -o promotion.md        # gate result, model card and approver checklist
smlops render-release --model-image "$MODEL_IMAGE" --server-image "$SERVER_IMAGE"
git switch -c promote-ticket-router-vN && git add deploy/gitops/production
git commit -m "Promote ticket-router vN" && gh pr create --body-file promotion.md
```

Both images must come from the cluster registry, pinned by digest; `render-release` refuses anything else, and
Kyverno checks the signatures again when Argo CD applies the release. Merging the pull request is the approval
(gate G4); CODEOWNERS names who may give it.
