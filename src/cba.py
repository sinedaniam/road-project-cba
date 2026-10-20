"""Economic cost-benefit appraisal of a road upgrade (illustrative case).

Models a hypothetical 50 km gravel-to-bitumen upgrade and computes NPV,
EIRR, BCR and first-year return, followed by sensitivity, switching-value
and Monte Carlo risk analysis. All inputs are assumptions in ASSUMPTIONS
below, in constant 2026 prices (KES million unless stated).

Run from the repo root:  python src/cba.py
"""
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "figures"
RESULTS = ROOT / "results"


@dataclass(frozen=True)
class Assumptions:
    length_km: float = 50
    capex_per_km: float = 90.0            # KES m/km, economic cost (net of taxes)
    capex_phasing: tuple = (0.3, 0.4, 0.3)  # share of capex in construction years 1-3
    base_aadt: float = 1_800              # vehicles/day in the opening year
    traffic_growth: float = 0.05          # per year
    generated_traffic: float = 0.10       # extra trips induced by the upgrade
    voc_saving_per_veh_km: float = 0.000022   # KES m (= KES 22) per vehicle-km
    time_saving_hours_per_trip: float = 0.35
    value_of_time_per_hour: float = 0.00025  # KES m (= KES 250) per vehicle-hour
    accident_saving_per_year: float = 6.0    # KES m in the opening year, grows with traffic
    maint_without: float = 1.2            # KES m/km/yr routine + regravelling, gravel road
    maint_with: float = 0.5               # KES m/km/yr routine, bitumen road
    periodic_with: float = 8.0            # KES m/km resurfacing every 8 years
    periodic_cycle: int = 8
    discount_rate: float = 0.12
    operation_years: int = 20
    residual_value_share: float = 0.25    # of capex, credited in the final year


ASSUMPTIONS = Assumptions()


def cash_flows(a: Assumptions) -> pd.DataFrame:
    build = len(a.capex_phasing)
    years = np.arange(1, build + a.operation_years + 1)
    capex_total = a.length_km * a.capex_per_km
    df = pd.DataFrame(index=pd.Index(years, name="year"))
    df["capex"] = 0.0
    df.loc[1:build, "capex"] = [capex_total * s for s in a.capex_phasing]

    op = df.index > build
    t = np.where(op, df.index - build - 1, 0)
    aadt = a.base_aadt * (1 + a.traffic_growth) ** t
    veh_km = aadt * 365 * a.length_km
    trips = aadt * 365
    # Generated traffic gets half the per-trip benefit (rule of half).
    uplift = 1 + a.generated_traffic / 2

    df["voc_savings"] = np.where(op, veh_km * a.voc_saving_per_veh_km * uplift, 0)
    df["time_savings"] = np.where(op, trips * a.time_saving_hours_per_trip * a.value_of_time_per_hour * uplift, 0)
    df["accident_savings"] = np.where(op, a.accident_saving_per_year * (1 + a.traffic_growth) ** t, 0)

    periodic = np.where(op & ((t + 1) % a.periodic_cycle == 0), a.periodic_with * a.length_km, 0)
    df["maintenance_savings"] = np.where(op, (a.maint_without - a.maint_with) * a.length_km, 0) - periodic
    df["residual_value"] = 0.0
    df.loc[df.index.max(), "residual_value"] = a.residual_value_share * capex_total

    df["benefits"] = df[["voc_savings", "time_savings", "accident_savings", "residual_value"]].sum(axis=1)
    df["costs"] = df["capex"] - df["maintenance_savings"]
    df["net"] = df["benefits"] - df["costs"]
    return df


def discount(values, r):
    return np.sum(np.asarray(values) / (1 + r) ** np.arange(1, len(values) + 1))


def eirr(net) -> float:
    lo, hi = -0.5, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if discount(net, mid) > 0 else (lo, mid)
    return mid


def indicators(a: Assumptions) -> dict:
    df = cash_flows(a)
    r = a.discount_rate
    pv_b, pv_c = discount(df["benefits"], r), discount(df["costs"], r)
    first_op = len(a.capex_phasing) + 1
    return {
        "NPV (KES m)": pv_b - pv_c,
        "EIRR": eirr(df["net"]),
        "BCR": pv_b / pv_c,
        "FYRR": df.loc[first_op, "net"] / df["capex"].sum(),
    }


SENSITIVITY = {
    "Capital cost +20%": dict(capex_per_km=ASSUMPTIONS.capex_per_km * 1.2),
    "Traffic -20%": dict(base_aadt=ASSUMPTIONS.base_aadt * 0.8),
    "Traffic growth 3%": dict(traffic_growth=0.03),
    "VOC savings -25%": dict(voc_saving_per_veh_km=ASSUMPTIONS.voc_saving_per_veh_km * 0.75),
    "Value of time -25%": dict(value_of_time_per_hour=ASSUMPTIONS.value_of_time_per_hour * 0.75),
    "Discount rate 10%": dict(discount_rate=0.10),
    "Discount rate 15%": dict(discount_rate=0.15),
}


def switching_value(field: str, a: Assumptions) -> float:
    """Multiplier on `field` at which NPV becomes zero."""
    base = getattr(a, field)
    lo, hi = 0.01, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        npv = indicators(replace(a, **{field: base * mid}))["NPV (KES m)"]
        positive_at_mid = npv > 0
        increasing_hurts = field == "capex_per_km"
        if positive_at_mid == increasing_hurts:
            lo = mid
        else:
            hi = mid
    return mid


def monte_carlo(a: Assumptions, n: int = 5_000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n):
        s = replace(
            a,
            capex_per_km=a.capex_per_km * rng.triangular(0.9, 1.0, 1.5),   # overruns skew upward
            base_aadt=a.base_aadt * rng.normal(1.0, 0.15),
            traffic_growth=rng.triangular(0.02, 0.05, 0.07),
            voc_saving_per_veh_km=a.voc_saving_per_veh_km * rng.normal(1.0, 0.15),
            value_of_time_per_hour=a.value_of_time_per_hour * rng.normal(1.0, 0.20),
        )
        draws.append(indicators(s))
    return pd.DataFrame(draws)


def main():
    FIG.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    a = ASSUMPTIONS

    flows = cash_flows(a)
    flows.round(1).to_csv(RESULTS / "cash_flows.csv")
    base = indicators(a)
    print("Base case:", {k: round(v, 3) for k, v in base.items()})

    sens = pd.DataFrame({"Base case": base} | {k: indicators(replace(a, **v)) for k, v in SENSITIVITY.items()}).T
    sens.round(3).to_csv(RESULTS / "sensitivity.csv")
    print("\nSensitivity:\n", sens.round(3))

    switching = {
        "Capital cost": switching_value("capex_per_km", a),
        "Opening-year traffic": switching_value("base_aadt", a),
        "VOC saving per veh-km": switching_value("voc_saving_per_veh_km", a),
    }
    sw = pd.Series(switching, name="multiplier_for_npv_zero").round(3)
    sw.to_csv(RESULTS / "switching_values.csv")
    print("\nSwitching values (multiplier on base):\n", sw)

    mc = monte_carlo(a)
    mc.describe(percentiles=[0.05, 0.5, 0.95]).round(3).to_csv(RESULTS / "monte_carlo_summary.csv")
    p_neg = (mc["NPV (KES m)"] < 0).mean()
    print(f"\nMonte Carlo: P(NPV < 0) = {p_neg:.1%}, median EIRR = {mc['EIRR'].median():.1%}")

    # Figure 1: tornado of NPV changes.
    delta = (sens["NPV (KES m)"].drop("Base case") - base["NPV (KES m)"]).sort_values()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(delta.index, delta.values, color=np.where(delta < 0, "#c0504d", "#1f4e79"))
    ax.axvline(0, color="black", lw=0.8)
    ax.set(title=f"Change in NPV from base case (base NPV = KES {base['NPV (KES m)']:,.0f} m)",
           xlabel="KES million")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "sensitivity_tornado.png", dpi=150)

    # Figure 2: Monte Carlo NPV distribution.
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(mc["NPV (KES m)"], bins=60, color="#1f4e79", alpha=0.85)
    ax.axvline(0, color="#c0504d", lw=2, label=f"NPV = 0  (P(NPV<0) = {p_neg:.1%})")
    ax.set(title="Monte Carlo distribution of NPV (5,000 draws)", xlabel="NPV, KES million", ylabel="Draws")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "npv_distribution.png", dpi=150)

    # Figure 3: annual benefit streams vs costs.
    fig, ax = plt.subplots(figsize=(9, 4))
    parts = ["voc_savings", "time_savings", "accident_savings"]
    bottom = np.zeros(len(flows))
    for col, c in zip(parts, ["#1f4e79", "#4f81bd", "#9bbb59"]):
        ax.bar(flows.index, flows[col], bottom=bottom, color=c, label={"voc_savings": "Vehicle operating cost savings", "time_savings": "Travel time savings", "accident_savings": "Accident cost savings"}[col])
        bottom += flows[col].values
    ax.bar(flows.index, -flows["capex"], color="#c0504d", label="Capital cost")
    ax.axhline(0, color="black", lw=0.8)
    ax.set(title="Annual economic benefits and capital costs", xlabel="Project year", ylabel="KES million")
    ax.legend(frameon=False, fontsize=9, ncol=2)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG / "cash_flows.png", dpi=150)


if __name__ == "__main__":
    main()
