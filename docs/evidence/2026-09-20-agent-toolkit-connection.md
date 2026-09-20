# Connecting the coding agent to AWS — Agent Toolkit for AWS

- **Date**: 2026-09-20
- **Agent**: Claude Code
- **Profile**: `ferrenafe` · **Region**: `us-east-1` · **Identity**:
  `arn:aws:iam::<account>:user/akemid`
- **Instructions followed**:
  [`aws/agent-toolkit-for-aws` → `setup-instructions/setup.md`](https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md)
- **Outcome**: connected and verified, after two environment corrections

The Agent Toolkit is the official mechanism: an AWS MCP Server plus AWS-authored
skills, installed with `aws configure agent-toolkit`.

## Inputs

Gathered before running anything, as the instructions require: profile
`ferrenafe`, advanced AWS experience, region `us-east-1`, OS detected as macOS.
No access key or secret was requested or supplied at any point — that is the
point of the flow.

## Step 2 — AWS CLI

The toolkit needs **2.35.0 or later**. The machine had **2.34.35**, installed
system-wide at `/usr/local/aws-cli`, and it simply has no `agent-toolkit`
subcommand:

```
$ aws configure agent-toolkit help
aws: [ERROR] An error occurred (ParamValidation): argument subcommand:
Found invalid choice 'agent-toolkit'
```

The installer was downloaded and read before running, rather than piped
straight into a shell — the instructions explicitly sanction this. It fetches
only from `awscli.amazonaws.com`, and without `--system` installs user-local
with no root.

```
$ bash install.sh
PKG code signature verified (team 94KV3E626L).
AWS CLI 2.36.49 installed to ~/.local/share/aws-cli
```

## Step 3 & 4 — Authentication

```
$ aws configure set region us-east-1 --profile ferrenafe
$ aws login --region us-east-1 --profile ferrenafe
Updated profile ferrenafe to use arn:aws:iam::<account>:user/akemid credentials.

$ aws sts get-caller-identity --profile ferrenafe
  Arn : arn:aws:iam::<account>:user/akemid

$ aws configure list --profile ferrenafe
access_key : ****OEX4 : login      ← not a static key
secret_key : ****YIfV : login
region     : us-east-1 : config-file
```

Credential **type is `login`** — browser-issued, valid 12 hours, renewable for
90 days. This replaced a long-lived `AKIA…` access key as this project's
working credential.

## Step 5 — Agent Toolkit

```
$ aws configure agent-toolkit --yes --region us-east-1 --profile ferrenafe

Detecting installed AI coding agents...
  ✓ Claude Code, Cline, Codex, Cursor, Gemini CLI, Kiro, OpenCode, Pi, Windsurf

Installing 23 default AWS skills...
  amazon-bedrock, aws-ai-ml, aws-auth, aws-billing-and-cost-management,
  aws-blocks, aws-cdk, aws-cloudformation, aws-compute, aws-containers,
  aws-database, aws-deployment, aws-iam, aws-messaging-and-streaming,
  aws-networking, aws-observability, aws-sdk-js-v3-usage,
  aws-sdk-python-usage, aws-sdk-swift-usage, aws-security, aws-serverless,
  aws-storage, launch-with-aws, signing-in-to-aws

AWS MCP server configured for:
  ✓ Claude Code — ~/.claude.json: updated
  ✓ Cline, Cursor, Gemini CLI, Kiro, OpenCode, Windsurf
```

The toolkit's generated entry points at the `default` profile, which is wrong
here — this project authenticates under a named one. Per the instructions, only
an `env` block was added, leaving `command` and `args` byte-identical (asserted
in the edit script, with a backup taken first):

```json
"env": { "AWS_MCP_PROXY_PROFILES": "ferrenafe" }
```

Note: the service is only available in `us-east-1` regardless of the account's
region, so that value is not a coincidence of ours happening to match.

## Step 6 — Verification

```
$ aws agent-toolkit list-available-skills --region us-east-1 --profile ferrenafe
  105 skills in the remote catalogue
    amazon-aurora-mysql        v1
    amazon-aurora-postgresql   v1
    amazon-bedrock             v6
    amazon-braket              v1
    amazon-documentdb          v2
    amazon-dynamodb            v2
    ...
```

## Step 7 — Rules

No `CLAUDE.md` existed at the project root, so one was created. The AWS rules
sit between `<!-- BEGIN/END AWS Agent Toolkit rules -->` markers so a later
re-run replaces that block instead of appending a second copy, and the
project's own instructions sit above them — the AWS rules themselves say the
project's take precedence.

## Two environment corrections worth recording

**1. `PATH` order silently kept the old CLI.** `~/.zshrc` appended
`~/.local/bin` to the *end* of `PATH`, so `/usr/local/bin/aws` (2.34.35) still
won. The instructions prescribe *prepending*. Both files were corrected, with
the prepend in `.zshenv` rather than `.zshrc` because **`.zshrc` is read only
by interactive shells** — a non-interactive `zsh -c` still resolved the old
binary.

The verification itself was wrong twice before it was right. `zsh -l -c` is a
login shell but **not** interactive, so it never reads `.zshrc` and reported a
failure that was not real; testing only the interactive case would have
reported a success that was not general. What matters is all four
login × interactive combinations:

```
non-interactive     : 2.36.49   ✅
interactive         : 2.36.49   ✅
login + interactive : 2.36.49   ✅   ← a real new terminal
login only          : 2.34.35        ← .zprofile re-prepends Python; unused path
```

Two installations now coexist. Removing `/usr/local/aws-cli` needs root and is
the cleaner fix; `PATH` ordering is the workaround.

**2. A token-compressing command proxy rewrites `aws` calls** and returns the
response *schema* instead of the values. It survives redirection and piping, so
scripts parse the schema and fail — or read nothing, silently. `rtk proxy <cmd>`
bypasses it. Recorded in
[`2026-09-20-preflight.md`](2026-09-20-preflight.md) too, because it will bite
anything automating AWS reads here.

## What this establishes

The coding agent reads current AWS documentation, loads AWS-authored skills,
and calls AWS APIs under a named profile with short-lived credentials scoped to
this user's own permissions — as
[Understanding IAM for Managed AWS MCP Servers](https://docs.aws.amazon.com/)
describes, the agent can do only what the signed-in identity can do.
