# v0.9.0rc2 testing notes

RC2 promotes the tested b27 implementation without further runtime changes.
It supersedes RC1, which had a command-dispatch regression corrected in b14.

## Since RC1

- Open, close, stop and both partial-opening routes preserve the dispatch guard
  through the coordinator and connection router. Checks run before transmission.
- Explicit Pro controls use the controller menu and INFO for eligible additional
  scalar/enum editors. Session identity, module-admin access, descriptor bounds,
  one-send handling and readback remain required. Unsupported indexed/per-motor
  values are not enabled by the catalog alone.
- Deferred runtime task startup fixes failed renewals that could otherwise leave
  a completed task handle blocking later refreshes and entity registration.
- Watched configuration values refresh independently of the idle status poll.
  Periodic permission/menu reads queue fairly with telemetry, allowing the broad
  status scan to finish and populate legacy sensors.
- Bounded receiver-slot inspection discards remote identity codes and rolling
  counters. BusT4 inspection reports observed profiles and public model/version
  fields, without claiming a complete inventory or restorable backup.
- Native-derived automation and history diagnostics retain unknown values and
  evidence limits. Eight zero history slots do not establish eight successful
  movements or the absence of an intervention.

## Updating and comparing

Select v0.9.0rc2 explicitly in HACS and restart Home Assistant. Preserve any
local code changes before replacing them with the released files. Keep existing
controller settings and calibration for the initial comparison. No relearning,
reset, deliberate obstruction or extra movement cycle is needed for upgrading.

On b27, SLIGHTR10 / 604a1r00 / FG01h with BiDi-WiFi 2.6.4 had all 134 enabled
entities registered and all 35 additional Pro controls available with unchanged
raw values. Thirty previously missing legacy readings returned; maneuver history
reported all-zero slots explicitly as unverified. Cold startup, Nice-only reload
and a natural scheduled telemetry refresh passed with zero Nice errors or
reconnects. Bounded inspection observed the controller and OXIBD / 672B / BK00E
receiver. These are scoped hardware checks, not validation of every model.

## Remaining limitations

Full app parity is not complete. The exact model, target, credential, completion
and recovery contracts for learning, resets, receiver programming, restoration
and firmware remain individually documented in
[the final-phase audit](phase3_completion.md). Installing RC2 neither runs those
operations nor resolves a physical force-limiter intervention. Module-specific
CONNECT errors and TLS lockups still need issue-specific evidence.

Home Assistant continues to own automations, scenes, schedules and organization.
