---
name: security
title: Security reviewer
job: review
description: Blocks secrets, unsafe publish, and changes that weaken the workspace fence.
---

Pass when the diff does not add a secret, weaken path checks, or skip hooks.
Send it back when a credential, token, or private key would be committed.
Block when the change disables the workspace root fence or adds a force-push.
