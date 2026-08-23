# Standalone logic test for analyze.py against planted synthetic data (no DB needed).
# Run from the mldb/ directory:  python3 experiments/test_analyze.py
import sys, json, math
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from experiments.results import ResultsWriter, TrialResult
from experiments import analyze

P = "/tmp/res_test.jsonl"
import os; os.path.exists(P) and os.remove(P)

_T=[100.0]
def tick():
    _T[0]+=37.0; return _T[0]
def ac(w, scen, scale, seed, windows, confirmed=(True,True,True), lat=0.02, offsets=(0.02,0.5,1.0), extra_c=None):
    t=tick()
    for i, layer in enumerate(analyze.LAYERS):
        e = {"revoke_latency_s": lat, "poll_start_offset_s": offsets[i]}
        if extra_c: e.update(extra_c)
        w.write(TrialResult(scenario=scen, scale=scale, seed=seed, resource_id=f"r{seed}", principal_id=f"p{seed}",
            layer=layer, t_issued=t, leak_window_s=windows[i], confirmed_contained=confirmed[i],
            checks_performed=10, extra=e))

with ResultsWriter(P) as w:
    # ---- Scenario A, scale 1000: seeds 1..3, drift = max over layers -> [3.0, 0.6, 2.0]; median 2.0
    ac(w, "a", 1000, 1, (1.0, 3.0, 2.0))
    ac(w, "a", 1000, 2, (0.0, 0.6, 0.0))          # two censored zeros (nosql? no -> relational, vector)
    ac(w, "a", 1000, 3, (2.0, 0.5, 0.1))
    # scale 10000: one timeout on vector -> excluded from drift, n_timeout=1
    ac(w, "a", 10000, 1, (1.0, 1.0, 30.0), confirmed=(True,True,False))
    ac(w, "a", 10000, 2, (4.0, 1.0, 1.0))
    # ---- Scenario C
    st_ok = {"contained": True, "missingLayers": []}
    st_no = {"contained": False, "missingLayers": ["vector"]}
    prop_ok = [{"layer": l, "ok": True, "attempts": 1, "anchored": True} for l in analyze.LAYERS]
    prop_anchorfail = [dict(p) for p in prop_ok]; prop_anchorfail[2]["anchored"] = False; prop_anchorfail[2]["anchorError"] = "boom"
    # 1 true containment (all closed, bridge contained); windows < latency -> no post-claim leak
    ac(w, "c", 1000, 1, (0.01, 0.01, 0.01), lat=0.05, extra_c={"bridge_status": st_ok, "bridge_response": {"fullyPropagated": True, "propagation": prop_ok}})
    # 2 FALSE containment: bridge contained, vector timed out
    ac(w, "c", 1000, 2, (0.01, 0.01, 30.0), confirmed=(True,True,False), lat=0.05, extra_c={"bridge_status": st_ok, "bridge_response": {"fullyPropagated": True, "propagation": prop_ok}})
    # 3 false non-containment, anchor failed
    ac(w, "c", 1000, 3, (0.01, 0.01, 0.01), lat=0.05, extra_c={"bridge_status": st_no, "bridge_response": {"fullyPropagated": True, "propagation": prop_anchorfail}})
    # 4 false non-containment other (status says not contained, propagation all fine)
    ac(w, "c", 1000, 4, (0.01, 0.01, 0.01), lat=0.05, extra_c={"bridge_status": st_no, "bridge_response": {"fullyPropagated": True, "propagation": prop_ok}})
    # 5 true containment BUT post-claim leak: vector window 0.2 > latency 0.05, fullyPropagated True
    ac(w, "c", 1000, 5, (0.01, 0.01, 0.2), lat=0.05, extra_c={"bridge_status": st_ok, "bridge_response": {"fullyPropagated": True, "propagation": prop_ok}})
    # 6 status unavailable (error dict)
    ac(w, "c", 1000, 6, (0.01, 0.01, 0.01), lat=0.05, extra_c={"bridge_status": {"error": "status 502"}, "bridge_response": {"fullyPropagated": True, "propagation": prop_ok}})
    # 7 true non-containment
    ac(w, "c", 1000, 7, (0.01, 0.01, 30.0), confirmed=(True,True,False), lat=0.05, extra_c={"bridge_status": st_no, "bridge_response": {"fullyPropagated": False, "propagation": prop_ok}})
    # 9 revoke rejected fail-closed: HTTP error body, all layers time out; bridge says not contained
    ac(w, "c", 1000, 9, (30.0, 30.0, 30.0), confirmed=(False,False,False), lat=0.3, extra_c={"bridge_status": st_no, "bridge_response": {"error": "Connection refused"}})
    # 10 partial propagation (207): vector failed after retries; truth: vector times out; bridge not contained -> true non-containment + partial
    prop_partial=[dict(p) for p in prop_ok]; prop_partial[2]={"layer":"vector","ok":False,"attempts":3,"error":"x","anchored":False}
    ac(w, "c", 1000, 10, (0.01, 0.01, 30.0), confirmed=(True,True,False), lat=0.4, extra_c={"bridge_status": st_no, "bridge_response": {"fullyPropagated": False, "propagation": prop_partial}})
    # 8 incomplete trial (only 2 layers written -- crash mid-trial)
    t8=tick()
    for layer in ("relational", "nosql"):
        w.write(TrialResult(scenario="c", scale=1000, seed=8, resource_id="r8", principal_id="p8", layer=layer,
            t_issued=t8, leak_window_s=0.01, confirmed_contained=True, checks_performed=5, extra={}))
    # ---- B1 with interval 5: windows 1,2,6 (one exceeds interval)
    for seed, wdw in ((1,1.0),(2,2.0),(3,6.0)):
        w.write(TrialResult(scenario="b1", scale=10000, seed=seed, resource_id="r", principal_id="p", layer=None,
            t_issued=tick(), leak_window_s=wdw, confirmed_contained=True, checks_performed=5,
            extra={"refresh_interval_s": 5.0, "revoke_latency_s": 0.003}))
    # ---- B3 strict (zeros) / relaxed (0.1, 0.3)
    for seed in (1,2):
        w.write(TrialResult(scenario="b3", scale=10000, seed=seed, resource_id="r", principal_id="p", layer=None,
            t_issued=tick(), leak_window_s=0.0, confirmed_contained=True, checks_performed=5, extra={"relaxed_order": False, "revoke_latency_s": 0.004}))
        w.write(TrialResult(scenario="b3", scale=10000, seed=seed, resource_id="r", principal_id="p", layer=None,
            t_issued=tick(), leak_window_s=0.1*seed, confirmed_contained=True, checks_performed=5, extra={"relaxed_order": True, "revoke_latency_s": 0.004}))
    # ---- old-format B2 record (no revoke_latency_s)
    w.write(TrialResult(scenario="b2", scale=10000, seed=1, resource_id="r", principal_id="p", layer=None,
        t_issued=tick(), leak_window_s=0.05, confirmed_contained=True, checks_performed=5))
    # ---- B6: seed1 = 7/10 leaked, seed2 = 0/10
    for seed, leaks in ((1,7),(2,0)):
        for i in range(10):
            leaked = i < leaks
            w.write(TrialResult(scenario="b6", scale=10000, seed=seed, resource_id=f"r{i}", principal_id="p", layer=None,
                t_issued=tick(), leak_window_s=None, confirmed_contained=(not leaked), checks_performed=1, extra={"repeat_index": i, "leaked": leaked}))
    # ---- error record
    w.write(TrialResult(scenario="b4", scale=10000, seed=2, resource_id="", principal_id="", layer=None,
        t_issued=tick(), leak_window_s=None, confirmed_contained=None, checks_performed=None, extra={"error": "ConnectionRefused"}))
# truncated last line
with open(P, "a") as fh: fh.write('{"scenario": "a", "scale": 10')

recs, errs, skipped = analyze.load_results([P])
assert skipped == 1 and len(errs) == 1, (skipped, len(errs))
s = analyze.analyse(recs, errs)

# A drift
d = s["ac"]["a"]["drift"]["1000"]; assert d["n"]==3 and d["median"]==2.0 and d["max"]==3.0 and d["n_timeout"]==0, d
d = s["ac"]["a"]["drift"]["10000"]; assert d["n"]==1 and d["median"]==4.0 and d["n_timeout"]==1, d
# per-layer timeout exclusion
pl = s["ac"]["a"]["per_layer"]["10000|vector"]; assert pl["n"]==1 and pl["n_timeout"]==1 and pl["max"]==1.0, pl
# censoring: A@1000 relational has one zero (seed2), bound 0.02; vector zero bound 1.0
c = s["ac"]["a"]["per_layer"]["1000|relational"]["censoring"]; assert c["n_zero"]==1 and c["max_censor_bound_s"]==0.02, c
c = s["ac"]["a"]["per_layer"]["1000|vector"]["censoring"]; assert c["n_zero"]==1 and c["max_censor_bound_s"]==1.0, c
c = s["ac"]["a"]["per_layer"]["1000|nosql"]["censoring"]; assert c["n_zero"]==0, c
# false containment cells
fc = s["false_containment"]["c"]; cells = fc["cells"]
assert cells == {"true_containment":2, "false_containment":1, "false_non_containment_anchor_failed":1,
                 "false_non_containment_anchor_pending":0, "false_non_containment_other":1,
                 "invalid_setup_trials":0,
                 "true_non_containment":2, "revoke_rejected_fail_closed":1,
                 "bridge_status_unavailable":1, "incomplete_trials":1}, cells
assert cells["false_non_containment_anchor_pending"] == 0, "sync-mode records must not land in the async bucket"
# A trial whose record was never retrievable must be excluded, not counted as
# a containment success: a mis-seeded run would otherwise manufacture them.
import copy as _copy
_recs = _copy.deepcopy(recs)
_hit = 0
for _r in _recs:
    if _r["scenario"] == "c" and _hit < 3:
        _r["extra"]["retrievable_before_revoke"] = False
        _hit += 1
_c2 = analyze.analyse(_recs, errs)["false_containment"]["c"]["cells"]
assert _c2["invalid_setup_trials"] >= 1, _c2
assert _c2["true_containment"] < cells["true_containment"], (_c2, cells)
assert fc["n_partial_propagation"]==2  # trials 7 and 10 both have fullyPropagated=False
assert fc["n_classified"]==7
assert fc["time_resolved"]["n_fully_propagated_claims"]==6 and fc["time_resolved"]["n_post_claim_leaks"]==2, fc["time_resolved"]
# trial2 (vector 30s>0.05) and trial5 (0.2>0.05) are post-claim leaks; trial3,4,6 fullyPropagated but windows 0.01<0.05 -> no
assert sorted(t["trial"][2] for t in fc["time_resolved"]["post_claim_leak_trials"]) == [2,5]
# B1
b1 = s["b"]["b1"]["all"]; assert b1["n"]==3 and b1["median"]==2.0 and b1["n_exceeding_interval"]==1 and b1["median_over_interval"]==0.4, b1
# B3 split
assert s["b"]["b3"]["strict"]["n"]==2 and s["b"]["b3"]["strict"]["max"]==0.0
assert s["b"]["b3"]["relaxed_order"]["n"]==2 and abs(s["b"]["b3"]["relaxed_order"]["median"]-0.15)<1e-9
# B6
b6 = s["b"]["b6"]; assert b6["per_scale_seed"]["10000|1"]["rate"]==0.7 and b6["per_scale_seed"]["10000|2"]["rate"]==0.0 and b6["pooled"]["leaked"]==7 and b6["pooled"]["calls"]==20
lo,hi = b6["per_scale_seed"]["10000|2"]["wilson_ci95"]; assert lo==0.0 and 0.2<hi<0.35, (lo,hi)  # Wilson for 0/10 ~ [0, 0.278]
# overhead: A dedup per trial -> 5 trials, C -> 7 (trial 8 has no latency)
assert s["overhead"]["a"]["1000"]["n"]==3 and s["overhead"]["a"]["10000"]["n"]==2
assert s["overhead"]["c"]["1000"]["n"]==9, s["overhead"]["c"]
# drift for C: 9 complete trials (1..7,9,10); rejected=1 excluded; timeouts = trials 2,7,10 = 3; measured = 1,3,4,5,6 = 5
dc = s["ac"]["c"]["drift"]["1000"]; assert dc["n"]==5 and dc["n_timeout"]==3 and dc["n_revoke_rejected"]==1 and dc["n_incomplete_trials"]==1, dc
assert s["overhead"]["b3"]["10000"]["n"]==4  # strict+relaxed x 2 seeds = 4 distinct revokes
assert "b2" not in s["overhead"] and s["overhead"]["_n_records_without_latency"]==3  # b2(1) + c seed8 (2)
assert s["overhead"]["c_vs_a"]["c_vs_a"]["1000"]["c_over_a"]==2.5   # paired by regime: healthy C vs healthy A
# bootstrap determinism
assert analyze.bootstrap_ci([1,2,3,4,5]) == analyze.bootstrap_ci([1,2,3,4,5])
# wilson sanity
lo,hi = analyze.wilson_interval(50,100); assert abs(lo-0.4038)<0.001 and abs(hi-0.5962)<0.001
print("ALL LOGIC ASSERTIONS PASSED")

# full pipeline incl. report + figures
import shutil; shutil.rmtree("/tmp/an", ignore_errors=True)
analyze.write_report("/tmp/an", s, len(recs), len(errs), skipped, errs)
analyze.make_figures("/tmp/an", s)
json.load(open("/tmp/an/summary.json"))  # serialisable
print("PIPELINE OK")
