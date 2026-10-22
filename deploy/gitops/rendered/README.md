# Rendered releases, one directory per environment

The ApplicationSet ([`../argocd/applicationset.yaml`](../argocd/applicationset.yaml)) makes one Argo CD Application
for every `<env>/target.yaml` here, under that environment's project ([`../argocd/projects.yaml`](../argocd/projects.yaml)).
No environment has a release yet, so nothing is deployed from here; the CI cluster deploys `dev` from an OCI
artifact instead (docs/serving.md).

Do not edit files here by hand. A promotion renders them and opens a pull request:

```bash
smlops render-release --env staging --model-image "$MODEL_IMAGE" --server-image "$SERVER_IMAGE"
smlops promotion-request -o promotion.md        # gate result, model card and approver checklist
git switch -c promote-ticket-router-staging && git add deploy/gitops/rendered/staging
git commit -m "Promote ticket-router to staging" && gh pr create --body-file promotion.md
```

Each directory is the ticket-router chart rendered with that environment's values
([`../envs/<env>/values.yaml`](../envs/)), both images pinned by digest from the cluster registry; `render-release`
refuses anything else, and Kyverno checks the signatures again when Argo CD applies the release. Merging the pull
request is the approval (gate G4); CODEOWNERS names who may give it.
