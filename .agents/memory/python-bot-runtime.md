---
name: Python bot runtime
description: Runtime setup lessons for the standalone Python Discord service.
---

Python services in this workspace need an explicit Python toolchain installed before language-package installation succeeds. A Discord gateway bot should use a console workflow without a port; the workflow is considered healthy when its process stays running and its gateway login succeeds.

**Why:** The initial dependency installation failed before the Python 3.12 toolchain was installed, while the second attempt succeeded after the runtime was provisioned.

**How to apply:** When adding or repairing Python services, install the available Python module first, then install from requirements.txt, and configure a portless console workflow.