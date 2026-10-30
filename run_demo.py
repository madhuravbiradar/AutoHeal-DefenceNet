"""Headless demo (no web server, no sleeping): replays all 6 scenarios and prints engine vs baseline.
    python run_demo.py            # summary table
    python run_demo.py -v         # + full incident timelines
"""
import sys

from demo.compare import compare, run_engine
from demo.scenarios import SCENARIOS


def main():
    verbose = "-v" in sys.argv
    print("\nSELF-HEALING NETWORK - scenario replay (1 tick = 1 simulated second)\n")
    for row in compare():
        e, b = row["engine"], row["baseline"]
        print(f"{row['title']}\n  {row['story']}")
        print(f"  {'':22}{'ENGINE':>10}{'BASELINE':>12}")
        print(f"  {'service downtime (s)':22}{e['downtime_s']:>10}{b['downtime_s']:>12}")
        print(f"  {'healthy at the end':22}{str(e['final_ok']):>10}{str(b['final_ok']):>12}")
        print(f"  {'fixes proven':22}{e['verified']:>10}{'n/a':>12}")
        print(f"  {'evidence records':22}{e['evidence_records']:>10}{b['evidence_records']:>12}")
        print(f"  {'operator actions':22}{e['operator_actions']:>10}{'n/a':>12}")
        print(f"  {'false healed claims':22}{0:>10}{b['false_success_claims']:>12}\n")
        if verbose:
            for inc in run_engine(row["scenario"])["orch"].incidents.values():
                print(f"    Incident #{inc['id']} {inc['fault']} {inc['first_target']} -> {inc['status']}")
                for t in inc["timeline"]:
                    print(f"      t={t['tick']:>3} [{t['kind']:<9}] {t['text']}")
            print()


if __name__ == "__main__":
    main()
