# Lab observability operations

## Installation gate

1. Select the exact pCloud revision and reviewed uncommitted patch if any.
   Record rendered artifact hash, actual cluster/context/namespace, component
   locks, worker capacity and M3a/M3b/M3c accepted handovers. Examples do not
   authorize deployment. Confirm filesystem readiness and UID 10001 access.
2. Deliver the four Secrets using the accepted custody process; check key
   names, file permissions, trusted TLS SAN/chain, receiver CA/token and distinct
   role hashes. Record Secret UIDs and custody/rotation ownership without values.
3. Confirm enforcing CNI policies, CoreDNS selector, worker topology, real
   consumer source CIDRs, receiver destination IPs and reachability. DNS selectors
   and IPBlocks must be checked against the target CNI; egress policies do not
   constrain DNS hostname rebinding by themselves. Confirm gateway routing.
4. Render to an operator artifact outside repositories. Inspect all 28 objects,
   existing names and labels: reject collisions or foreign resources. Run offline
   checks and read-only preflight. Pending backend claims are valid only before
   first-consumer binding; mounted probes and measured capacity must pass before
   telemetry acceptance. Inspect the diff against the cluster.
5. Obtain applicable owner approval naming that cluster, namespace, revision,
   digest, exposure and retained-data handling. The contract requires this
   separate gate. Then the authorized operator applies only this rendered
   artifact. No `--force`, prune or namespace deletion.
6. Observe five StatefulSets plus gateway Deployment; Grafana claim must bind
   Retain/WFFC on its selected worker. Capture all resource/PVC/PV UIDs and image
   IDs. Inspect readiness and suppressed/sanitized diagnostics if startup fails.
   Capture mounted free bytes/inodes, WAL paths and exact effective configs.
7. Run Live; set only role environment credentials and trust CA. Its INCOMPLETE
   outcome does not fail the read-only subchecks but cannot complete acceptance.
   Execute full acceptance in a separately reviewed disposable installation.

Example operator sequence from the package directory (substitute reviewed
context and an external artifact path; **apply is only after the gate above**):

```powershell
python observability.py render --site site.json --backend backend-capability.json --fragments backend-fragments.json > C:/operator-artifacts/m4-reviewed.yaml
python observability.py digest --site site.json --backend backend-capability.json --fragments backend-fragments.json
python observability.py preflight --site site.json --backend backend-capability.json --fragments backend-fragments.json
kubectl --context REVIEWED-CONTEXT diff -f C:/operator-artifacts/m4-reviewed.yaml
# Owner approval naming context, revision, digest and capacity/custody/exposure precedes apply.
kubectl --context REVIEWED-CONTEXT apply -f C:/operator-artifacts/m4-reviewed.yaml
kubectl --context REVIEWED-CONTEXT -n REVIEWED-NAMESPACE get statefulsets,deployments,pods,pvc
```

The operator artifact parent must already exist outside both repositories.
Windows PowerShell 5.1 redirection may write UTF-16; use PowerShell 7 or an
explicit UTF-8 writer. Treat `kubectl diff` exit 1 as a displayed difference,
not permission to apply it; nonzero validation/preflight failures stop apply.
Preflight intentionally returns 3 after its read-only subchecks: review the
remaining gates before seeking installation approval.

## Upgrade / restart

StatefulSets use OnDelete: applying new configuration does not restart them.
Only an authorized operator restarts one reviewed component at a time, naming
its current UID and expected template revision. Verify new image ID/config,
query retained synthetic data, ingest new signals, inspect backlog/drops, then
proceed. Gateway uses Recreate; brief ingest/query outage is expected. Secret
rotation is coordinated with the custody mechanism; nginx requires a reload or
restart, and Grafana encryption-key changes need its supported migration.
Do not treat mounted Secret changes as proof every process consumed them.

Pin one compatible release set, update source contracts/locks and tests, then
rehearse an isolated data upgrade. Never downgrade TSDB/schema/SQLite data
blindly. Record supported data-format compatibility before rollback.

## Rollback

Keep the prior reviewed manifest/version set outside source repositories in
the operator artifact store. If a startup/configuration regression occurs,
stop the affected component, compare current resource UIDs/owner labels, and
apply that reviewed prior component configuration only when data compatibility
is established. StatefulSets need the explicitly authorized restart afterwards.
Retained PVCs are never deleted by rollback. If a data migration cannot safely
be downgraded, leave the component stopped and recover into a new isolated
installation; do not rewrite another component's files. There is no declared
backup capability, so data lost from host/volume failure cannot be recovered
by this package. Define backup/restore capability before making a DR claim.

## Removal

Inventory package-labelled resources and verify each expected UID before any
operation. Stop/remove only the five StatefulSets, gateway Deployment, six
Services, six NetworkPolicies and nine ConfigMaps the approved inventory names.
Preserve the shared namespace, backend claims and Grafana claim/PV. Never
bulk-delete by namespace, delete a foreign object or alter Layer 0-2 resources.
Keep service/data-source/credential consumers informed; remove external exposure
only under its own scope. Retained Grafana/backend data and Secret custody need
an explicit separate disposition. No automated uninstall or PV reclamation is
provided. A retained directory is neither a backup nor a tested restore.

## Failure handling

An observability outage must not block application requests: consumers use
bounded asynchronous exporters. Queues/drop policy appear in the capability.
A backend outage can fill Collector queues; inspect accepted/exported/failed
signals and memory refusal, and compare to documented retry windows. The
platform health alert for backend loss depends on functioning Mimir/Collector;
this same-stack alerting cannot independently report a total stack/host loss.
An external availability monitor is needed for that condition.

No runbook step is evidence until performed against its named installation.
