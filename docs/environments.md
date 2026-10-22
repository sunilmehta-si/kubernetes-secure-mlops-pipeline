# Environments and GitOps delivery

One Helm chart, three environments, one directory per environment that Argo CD syncs. A release moves
dev → staging → prod by pull request, with exactly the same image digests at every step.

## Layout

```text
charts/ticket-router/            the only source of the serving manifests (values.schema.json, tested templates)
deploy/gitops/
├── envs/<env>/values.yaml       what differs per environment: replicas, resources. Never images.
├── envs/<env>/target.yaml       where it runs: cluster, namespace, Argo CD project
├── rendered/<env>/              the release: the chart rendered for <env>, images by digest; what Argo CD applies
└── argocd/
    ├── projects.yaml            one AppProject per environment, all with the same fence; prod adds sync windows
    └── applicationset.yaml      one Application per rendered/<env>/target.yaml
```

| Environment | Replicas | Resources (requests, limits) | Syncs |
|---|---|---|---|
| dev | 1 to 1 | 50m/384Mi, 500m/768Mi | at once; the CI cluster deploys it on every push |
| staging | 1 to 2 | the chart's production defaults | at once |
| prod | 2 to 4 | 250m/512Mi, 2 CPU/1Gi | weekdays 08:00 to 18:00 UTC; by hand at any time |

Staging has no overrides on purpose: anything set there would make staging prove less about prod.

## Why these choices

- **Folders, not branches.** Each environment is a directory on `main`. A branch per environment drifts, needs
  cherry-picks, and turns promotions into merges nobody can review.
- **Values only.** Templates never ask which environment they are in. Environments differ in a few reviewable
  lines, and the hardening is in the chart's defaults, so a forgotten override fails safe. Tests render the chart
  in every environment and check the hardening in each.
- **Rendered manifests.** CI renders the chart and the result is committed; Argo CD applies plain YAML. A pull
  request then shows the exact objects that will change, not only a values diff, and policies can be tested
  against the exact output. A test re-renders every committed release and fails on any difference, so a hand edit
  or an unrendered chart change cannot slip in.
- **The same digests everywhere.** `smlops promote` copies what the previous environment runs; nothing is rebuilt
  per environment, and nothing reaches prod without having run in staging.
- **Pinned Helm.** Another Helm version can render the same chart differently; `smlops` renders only with the
  version in `deploy/versions.env`.

## Promoting a release

```bash
smlops render-release --env dev --model-image "$MODEL_IMAGE" --server-image "$SERVER_IMAGE"   # a new release
smlops promote --from dev --to staging          # staging gets exactly dev's digests
smlops promote --from staging --to prod         # refused unless staging has a release; dev -> prod is refused
git switch -c promote-ticket-router-prod && git add deploy/gitops/rendered/prod
git commit -m "Promote ticket-router to prod" && gh pr create --body-file promotion.md
```

Merging is the approval (gate G4): CODEOWNERS covers `deploy/gitops/` and `charts/`. After the merge the
ApplicationSet's Application for that environment syncs it under its project, and Kyverno checks both signatures
and digests as the pods are created.

## On the CI cluster

There is no pull request to merge in CI, so the dev release travels as an OCI artifact in the cluster registry.
`smlops argocd-ci` fills in the ApplicationSet's template for dev and takes dev's project, changing only the
source, and the cluster workflow then drills the result ([serving.md](serving.md#delivery-with-gitops)).
