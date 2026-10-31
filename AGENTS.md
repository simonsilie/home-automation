# General rules

- Lead with the result. First sentence answers the question or states what changed.
- No preamble, no closing summary.

## Venus OS deploys

- When deploying `venus-os/` to the Cerbo, preserve `/data/etc/mqtt-grid-meter/config.ini` (it exists only on the device) and prefer a tar pipe over `scp -r` into an existing directory.
- Kill orphaned bridge processes before restarting the service: the D-Bus service name is held for the process's lifetime, not the directory's, so a new supervised copy crash-loops with `NameExistsException` until the orphan dies.

## Testing

- Unit tests verify real behaviour, not that mocks were called; mock only true I/O boundaries (D-Bus, MQTT, the clock) using small fakes.
- Prefer constructor injection over `unittest.mock.patch`.

## Git & commits

- Follow the conventional commit style: `<type>(<scope>): <subject>`.
  - Types: `feat`, `fix`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`.
  - Scope is a short area name, e.g. `venus-os`, `esphome`, `homeassistant`, `tools`.
  - Subject in imperative lowercase, no trailing period ("feat(venus-os): parse bridge config once into typed settings").
- The subject states what changed, not what was reviewed or refactored; put context in the body.
- Never commit without explicit permission in the current conversation.
