"""postflight's verified unit lists match the role flags that start each unit.

A unit its role manages conditionally must leave the list under the flags that
stop it, or the blocking deploy-verify-hosts job fails a deliberate state.
"""
from __future__ import annotations

import dataclasses
import itertools
import re
from dataclasses import dataclass
from pathlib import Path

import jinja2
import pytest
import yaml

from test_playbook_extra_var_bools import resolve_role

REPO = Path(__file__).resolve().parent.parent
POSTFLIGHT = REPO / "ansible/playbooks/postflight.yml"
UNIT_LIST_VAR = "postflight_active_units"
COLLECTION = "weisssrv.infra"
# A quoted unit name inside a templated list expression.
LITERAL = re.compile(r"'([A-Za-z][A-Za-z0-9._-]*)'")


@dataclass(frozen=True)
class Gate:
    """One role flag the unit's enable-and-start step reads.

    `default` is the collection's own default, None meaning the role ships
    none; `keeps` says whether a truthy value keeps the unit running.
    """

    variable: str
    role: str
    default: object
    keeps: bool


# Every unit postflight verifies, with the gates on its role's start step. Read
# off the collection: an empty tuple means the role starts the unit
# unconditionally, so the play asserts it on every host it selects.
UNIT_GATES: dict[str, tuple[Gate, ...]] = {
    "ssh": (),
    "postfix": (),
    "unbound": (),
    "AdGuardHome": (),
    "nfs-kernel-server": (
        Gate("nas_storage_exports", "nas_storage", None, True),
        Gate("nas_storage_skip_nfs_reload", "nas_storage", False, False),
    ),
    "smbd": (Gate("nas_storage_samba_shares", "nas_storage", None, True),),
    "nmbd": (
        Gate("nas_storage_samba_shares", "nas_storage", None, True),
        Gate("nas_storage_samba_disable_netbios", "nas_storage", True, False),
    ),
    "smartd": (
        Gate("nas_storage_smartd_enabled", "nas_storage", True, True),
        Gate("nas_storage_skip_smartd_service", "nas_storage", False, False),
    ),
    "tlshd": (Gate("nfs_tls_enabled", "nfs_tls", False, True),),
}

# A gate whose role ships no default is an `is defined` test, so its two states
# are a value and no variable at all.
DEFINEDNESS_PROBE = ["/export/probe"]


def unit_lists(path: Path = POSTFLIGHT) -> dict[str, object]:
    """play name -> its `postflight_active_units`, as written."""
    plays = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        play["name"]: play["vars"][UNIT_LIST_VAR]
        for play in plays
        if UNIT_LIST_VAR in (play.get("vars") or {})
    }


def units_named(declared: object) -> set[str]:
    """Every unit a list can yield: its items, or the literals in its template."""
    if isinstance(declared, list):
        return set(declared)
    return set(LITERAL.findall(declared))


def gates_of(declared: object) -> dict[str, Gate]:
    """variable -> Gate for every gate the units in one list are subject to."""
    return {
        gate.variable: gate
        for unit in units_named(declared)
        for gate in UNIT_GATES.get(unit, ())
    }


def render(declared: object, state: dict[str, bool]) -> set[str]:
    """The units a list yields, with each named gate variable on or off.

    A variable absent from `state` is left undefined, so the list's own
    `| default(...)` decides — which is how a real host without it renders.
    """
    if isinstance(declared, list):
        return set(declared)
    values = {}
    for variable, on in state.items():
        gate = gates_of(declared)[variable]
        if gate.default is None:
            if on:
                values[variable] = DEFINEDNESS_PROBE
        else:
            values[variable] = on
    environment = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    rendered = environment.from_string(declared).render(**values)
    return set(yaml.safe_load(rendered))


def predict(declared: object, state: dict[str, bool], table=None) -> set[str]:
    """The units the roles would actually have running in one flag state.

    `table` swaps in a mutated UNIT_GATES, so a case can prove the comparison
    reacts to a gate recorded with the wrong polarity.
    """
    expected = set()
    for unit in units_named(declared):
        gates = (table or UNIT_GATES)[unit]
        if all(state[gate.variable] == gate.keeps for gate in gates):
            expected.add(unit)
    return expected


def default_fallback_mismatch(declared: object, gate: Gate):
    """(yielded, expected) when one gate's spelled default misses the role's.

    Every other variable is on, so the unit is held back by this gate alone and
    a sibling `is defined` cannot mask a wrong `| default(...)`.
    """
    others = {
        variable: True for variable in gates_of(declared) if variable != gate.variable
    }
    yielded = render(declared, others)
    expected = predict(declared, {**others, gate.variable: bool(gate.default)})
    return None if yielded == expected else (yielded, expected)


def _role_defaults(role: str) -> dict:
    """One collection role's defaults/main.yml, fail-closed via resolve_role."""
    path = resolve_role(f"{COLLECTION}.{role}", REPO) / "defaults/main.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _role_task_text(role: str) -> str:
    """Every task and handler file of one role, concatenated."""
    directory = resolve_role(f"{COLLECTION}.{role}", REPO)
    return "\n".join(
        path.read_text(encoding="utf-8")
        for part in ("tasks", "handlers")
        for pattern in ("*.yml", "*.yaml")
        for path in sorted((directory / part).rglob(pattern))
        if path.is_file()
    )


GATED_UNITS = sorted(unit for unit, gates in UNIT_GATES.items() if gates)
ALL_GATES = sorted(
    {gate for gates in UNIT_GATES.values() for gate in gates},
    key=lambda gate: (gate.role, gate.variable),
)


class TestDiscovery:
    """Guards against a vacuous pass: the parse must find the real lists."""

    def test_every_play_with_a_unit_list_is_read(self):
        lists = unit_lists()
        assert len(lists) >= 6, lists

    def test_the_nas_list_is_templated(self):
        """The one list carrying conditional units — a plain list there would
        make every render below a no-op."""
        nas = unit_lists()["Verify NAS storage"]
        assert isinstance(nas, str) and "{{" in nas, nas

    def test_every_verified_unit_is_classified(self):
        unknown = {
            (name, unit)
            for name, declared in unit_lists().items()
            for unit in units_named(declared)
            if unit not in UNIT_GATES
        }
        assert not unknown, (
            "a postflight list verifies a unit this gate does not classify: "
            f"{sorted(unknown)} — read its role's enable-and-start step and add "
            "the unit to UNIT_GATES, with the flags that step reads"
        )


class TestTableMatchesTheCollection:
    """The table is the contract, so it is held to the installed collection."""

    @pytest.mark.parametrize("gate", ALL_GATES, ids=lambda gate: gate.variable)
    def test_the_role_still_reads_the_gate_variable(self, gate):
        assert gate.variable in _role_task_text(gate.role), (
            f"{gate.role} no longer names {gate.variable}, so postflight gates "
            "its unit on a variable the role has renamed or dropped"
        )

    @pytest.mark.parametrize("gate", ALL_GATES, ids=lambda gate: gate.variable)
    def test_the_role_default_is_what_the_table_claims(self, gate):
        defaults = _role_defaults(gate.role)
        if gate.default is None:
            assert gate.variable not in defaults, (
                f"{gate.role} now ships a default for {gate.variable}, so it is "
                "no longer an `is defined` gate"
            )
        else:
            assert defaults[gate.variable] == gate.default


class TestListsTrackTheFlags:
    """Each list must yield exactly the units the roles leave running."""

    @pytest.mark.parametrize("play", sorted(unit_lists()))
    def test_the_list_matches_the_roles_in_every_flag_state(self, play):
        declared = unit_lists()[play]
        variables = sorted(gates_of(declared))
        mismatches = []
        for combination in itertools.product([False, True], repeat=len(variables)):
            state = dict(zip(variables, combination))
            rendered = render(declared, state)
            expected = predict(declared, state)
            if rendered != expected:
                mismatches.append((state, sorted(rendered), sorted(expected)))
        assert not mismatches, (
            f"{play} verifies units the roles do not start in that flag state:\n  "
            + "\n  ".join(
                f"{state}: list yields {got}, roles leave running {want}"
                for state, got, want in mismatches
            )
        )

    @pytest.mark.parametrize("play", sorted(unit_lists()))
    def test_the_list_spells_the_same_defaults_as_the_roles(self, play):
        """With no inventory override at all, the list must match what the role
        defaults leave running — the case a host inheriting every default hits."""
        declared = unit_lists()[play]
        gates = gates_of(declared)
        state = {
            variable: bool(gate.default) for variable, gate in gates.items()
        }
        rendered = render(declared, {})
        expected = predict(declared, state)
        assert rendered == expected, (
            f"{play} and the role defaults disagree on a host with no override: "
            f"list yields {sorted(rendered)}, roles leave running "
            f"{sorted(expected)}"
        )

    @pytest.mark.parametrize("gate", ALL_GATES, ids=lambda gate: gate.variable)
    def test_each_gate_falls_back_to_its_roles_default(self, gate):
        """A host that overrides every other flag but not this one: the list's
        own `| default(...)` must land where the collection's default does."""
        for play, declared in unit_lists().items():
            if gate.variable not in gates_of(declared):
                continue
            mismatch = default_fallback_mismatch(declared, gate)
            assert mismatch is None, (
                f"{play} spells a different default for {gate.variable} than "
                f"{gate.role} ships: with it unset the list yields "
                f"{sorted(mismatch[0])}, the roles leave running "
                f"{sorted(mismatch[1])}"
            )

    @pytest.mark.parametrize("baseline", ["keeps", "defaults"])
    @pytest.mark.parametrize("unit", GATED_UNITS)
    def test_a_conditional_unit_is_absent_in_its_stopped_state(self, unit, baseline):
        """Per unit, the state the roles stop it in yields a list without it,
        with the siblings both all overridden to keep their units running and
        each at its own role default: one baseline can hide a misread flag."""
        plays = {
            play: declared
            for play, declared in unit_lists().items()
            if unit in units_named(declared)
        }
        assert plays, f"{unit} is in UNIT_GATES but no postflight list names it"
        for play, declared in plays.items():
            for stopped in UNIT_GATES[unit]:
                state = {
                    gate.variable: (
                        gate.keeps if baseline == "keeps" else bool(gate.default)
                    )
                    for gate in gates_of(declared).values()
                }
                state[stopped.variable] = not stopped.keeps
                assert unit not in render(declared, state), (
                    f"{play} still verifies {unit} with "
                    f"{stopped.variable}={state[stopped.variable]} and its "
                    f"siblings at their {baseline} values, which "
                    f"{stopped.role} stops it in"
                )


UNGATED_NMBD = """\
{{ ['nfs-kernel-server', 'smbd', 'nmbd']
   + (['smartd'] if (nas_storage_smartd_enabled | default(true)) else [])
   + (['tlshd'] if (nfs_tls_enabled | default(false)) else []) }}
"""


def _mutate(play: str, old: str, new: str) -> str:
    """One play's list with `old` replaced, asserting the anchor was really there.

    A missing anchor would turn a mutation case into a silent no-op.
    """
    declared = unit_lists()[play]
    assert old in declared, f"{play} no longer spells `{old}`"
    return declared.replace(old, new, 1)


class TestTheComparisonIsNotDecorative:
    """Mutation cases: the shapes this gate exists to catch must fail it."""

    def test_the_pre_fix_nas_list_is_reported(self):
        """The list that failed deploy-verify-hosts: nmbd asserted active while
        the role default stops it."""
        state = {variable: True for variable in gates_of(UNGATED_NMBD)}
        state["nas_storage_samba_disable_netbios"] = True
        assert "nmbd" in render(UNGATED_NMBD, state)
        assert "nmbd" not in predict(UNGATED_NMBD, state)

    def test_a_default_that_disagrees_with_the_role_is_reported(self):
        """A gate spelled with the wrong default passes every explicit state,
        so only the fallback comparison catches it."""
        wrong = _mutate(
            "Verify NAS storage",
            "nas_storage_samba_disable_netbios | default(true)",
            "nas_storage_samba_disable_netbios | default(false)",
        )
        gate = next(
            g for g in ALL_GATES if g.variable == "nas_storage_samba_disable_netbios"
        )
        state = {variable: True for variable in gates_of(wrong)}
        assert render(wrong, state) == predict(wrong, state)
        assert default_fallback_mismatch(wrong, gate) is not None

    @pytest.mark.parametrize("gate", ALL_GATES, ids=lambda gate: gate.variable)
    def test_a_gate_recorded_with_the_wrong_polarity_is_reported(self, gate):
        """`keeps` is how this table records the polarity of the role's service
        condition, and the variable being named in the role proves nothing about
        it: flipping it must red the flag-state comparison."""
        flipped = dataclasses.replace(gate, keeps=not gate.keeps)
        table = {
            unit: tuple(flipped if g == gate else g for g in gates)
            for unit, gates in UNIT_GATES.items()
        }
        plays = [
            declared
            for declared in unit_lists().values()
            if gate.variable in gates_of(declared)
        ]
        assert plays, f"no postflight list is subject to {gate.variable}"
        agreed = disagreed = 0
        for declared in plays:
            variables = sorted(gates_of(declared))
            for combination in itertools.product([False, True], repeat=len(variables)):
                state = dict(zip(variables, combination))
                rendered = render(declared, state)
                assert rendered == predict(declared, state), (
                    f"the unflipped table already disagrees in {state}, so this "
                    "case cannot attribute a disagreement to the flip"
                )
                agreed += 1
                if rendered != predict(declared, state, table):
                    disagreed += 1
        assert agreed and disagreed, (
            f"flipping {gate.variable}.keeps changes no flag state, so the "
            "comparison does not actually check that polarity"
        )

    def test_dropping_the_smartd_service_gate_is_reported(self):
        """The second, independent gate on smartd: skipping its service step."""
        without = _mutate(
            "Verify NAS storage",
            "and not (nas_storage_skip_smartd_service | default(false))",
            "",
        )
        state = {variable: True for variable in gates_of(without)}
        assert "smartd" in render(without, state)
        assert "smartd" not in predict(without, state)
