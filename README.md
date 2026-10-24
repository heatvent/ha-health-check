# HA Health Check

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/heatvent/ha-health-check)](https://github.com/heatvent/ha-health-check/releases)
[![HA](https://img.shields.io/badge/Home%20Assistant-2025.1%2B-blue.svg)](https://www.home-assistant.io/)

**GitHub:** [github.com/heatvent/ha-health-check](https://github.com/heatvent/ha-health-check)

![HA Health Check](https://raw.githubusercontent.com/heatvent/ha-health-check/main/custom_components/house_health/icon.png)

HA Health Check builds a weekly checklist of what is broken, what is worth a look, and what is healthy. It does not turn devices on or off, and it does not treat "unavailable" or "this automation has not run lately" as a failure by itself.

The integration domain is `house_health`. An existing install updates in place.

---

## The report

Each report has four sections:

- **Fix** means something that should be working is broken.
- **Check** means look when you have a minute. An acknowledged alert stays here so a problem you tapped off does not disappear.
- **Left as-is** means the item is expected: powered off on purpose, ignored, or a known leftover.
- **Healthy** means the watched critical sensors, the email group, and the alerts are in their normal state.

The report is emailed to the notify entity chosen in Configure. Until you change it, that is the entity in `custom_components/house_health/const.py`. A copy is saved as the persistent notification `house_health`. The **Last report** sensor keeps that checklist across a restart.

## Schedule

The report runs every Sunday at 9:00 AM local time. Press **Run report** on the HA Health Check device, or call the `house_health.report` service, to run it sooner. Either one sends the email.

If Home Assistant is off at 9:00 AM, the next startup sends the report when the last one is at least 7 days old. A restart later the same day does not send another email.

## Ignore list

Open **Settings**, **Devices & services**, **HA Health Check**, **Configure**, **Ignore list**.

- A **platform** ignores that whole class, including devices that show up later.
- A **device** ignores only that one.

Energy cost sensors start ignored when no energy price is set. The unused Music Assistant players start ignored. Clearing a list and saving keeps it empty. Leaving the options unset uses those defaults.

## Email and critical sensors

Open **Settings**, **Devices & services**, **HA Health Check**, **Configure**, **Email and critical sensors**.

The weekly email goes to the notify entity selected there. Critical sensors start as the built-in list. Uncheck a sensor to stop watching it. Saving with none checked watches none. The state that counts as healthy for each sensor stays in the integration.

Expected-offline devices stay in `const.py`.

## Install

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/heatvent/ha-health-check` as category **Integration**.
3. Download **HA Health Check**.
4. Restart Home Assistant.
5. Add **HA Health Check** from **Settings**, **Devices & services**, **Add integration**. Skip this step if it is already set up. There is only one instance.

## Updates

HACS installs from GitHub releases, not from the default branch. The release tag matches `version` in `manifest.json`, for example release `v1.1.0` is version `1.1.0`.

After HACS downloads an update, restart Home Assistant to load it.

## Links

- Repository: https://github.com/heatvent/ha-health-check
- Issues: https://github.com/heatvent/ha-health-check/issues
- Releases: https://github.com/heatvent/ha-health-check/releases
