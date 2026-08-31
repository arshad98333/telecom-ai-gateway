# The Production Grade Build Guidebook

### A step by step working method for any software project, in any language, from the first line of code to a repository that a stranger can clone, run, verify and pay for

---

## How to use this document

This is a working method, not a reading document. It is written as an ordered set of steps with a clear finish line for each one. Follow the steps in order on a new project. On an existing project, start at Step 0, run the audit, and fix whatever the audit finds before adding new features.

Three rules apply throughout.

**Rule one. Nothing is done until a machine that has never seen the project can prove it.** Your own computer is not evidence. A clean checkout on a clean machine, running one documented command, is evidence.

**Rule two. Every change arrives with the test that proves it.** A change without a test is a claim. A change with a test is a fact. This single habit drives quality, review speed, refactoring confidence and the commercial value of the repository history.

**Rule three. Small and continuous beats large and occasional.** Twenty small changes over four weeks is a healthier and more valuable project than one large change in a single day, even when the end state of the code is identical.

The last section of this guidebook is a prompt block. Paste it at the start of any session with an AI coding assistant so the assistant works to this standard instead of producing code that looks finished and is not.

---

# PART ONE. BEFORE THE FIRST LINE OF CODE

## Step 0. Write down what you are building and what done means

Do this before opening an editor. Ten minutes here saves weeks later.

Create a file named `docs/scope.md` and answer six questions in plain sentences.

1. What problem does this solve, and for whom.
2. What are the inputs, and where do they come from.
3. What are the outputs, and who consumes them.
4. What is explicitly out of scope for version one.
5. What does a correct result look like, stated so precisely that a test could check it.
6. What happens when things fail, and who needs to know.

**Finish line.** A person who has never spoken to you can read `docs/scope.md` and describe the system back to you correctly.

**What goes wrong without this.** The project drifts. Features get half built because nobody wrote down that they were out of scope. Tests get written against whatever the code happens to do rather than against what the system is supposed to do.

## Step 1. Choose the boring stack and pin it

Pick the language version, the framework, the database and the runtime before writing code, and write the exact versions into a file called `.tool-versions` or the equivalent version file for your ecosystem.

Prefer well used and well documented tools over new and clever ones. The cost of a tool is not the day you adopt it. It is every day afterwards, when you need answers, hires, security patches and upgrade paths.

Write down, in `README.md`, the exact versions of everything a developer needs installed before they can start. Language runtime. Package manager. Container tool. Database. Nothing implied, nothing assumed.

**Finish line.** The README lists exact versions, and a version manager file pins the language runtime so everyone gets the same one automatically.

**Edge cases most people miss.**

- The language version is pinned but the package manager version is not, so two developers resolve different dependency trees.
- The README says "recent version" instead of a number.
- The continuous integration service silently uses a different runtime version from the one developers use locally.

## Step 2. Create the repository skeleton in the first commit

Your very first commit should contain no product code. It should contain the shape of the project. This makes the project navigable from the first day and it makes every later commit small and readable.

Create this structure, adapting names to your ecosystem conventions but keeping the separation.

```
.
├── README.md                  what this is, how to run it, how to test it
├── LICENSE                    the legal terms, chosen deliberately
├── CHANGELOG.md               what changed in each released version
├── CONTRIBUTING.md            how to work on this, branch and commit rules
├── .gitignore                 build output, secrets, local files
├── .editorconfig              indentation and line endings for all editors
├── .env.example               every environment variable, with safe dummy values
├── Makefile or task file      one command per common action
├── docs/
│   ├── scope.md               what this is for
│   ├── architecture.md        the shape of the system and why
│   └── decisions/             one short file per significant decision
├── src/ or the language default
│   ├── config/                settings loading and validation, nothing else
│   ├── domain/                the rules of the problem, no input output
│   ├── adapters/              database, network, filesystem, third parties
│   ├── api/ or cli/           the entry points
│   └── observability/         logging, metrics, health checks
├── tests/
│   ├── unit/                  fast, no network, no database
│   ├── integration/           real database, stubbed third parties
│   └── fixtures/              small committed sample data
├── scripts/                   maintenance and one off tools
└── .github/workflows/         automated checks
```

**Finish line.** The first commit creates this tree with placeholder files, and the project builds and its empty test suite passes.

**Why this ordering matters.** The separation between `domain` and `adapters` is the single most valuable structural decision in most projects. Business rules that do not touch the network or the database can be tested in milliseconds, without setup, forever. Everything else can be swapped out.

## Step 3. Make one command do each common thing

Create a `Makefile`, or the task runner file your ecosystem prefers, with these targets. The names matter less than the fact that they exist and are identical for everyone.

```
make install      install everything needed to work on this
make dev          run the app locally with reload
make test         run the full test suite
make test-fast    run only the fast tests
make lint         check style and common mistakes
make format       fix style automatically
make typecheck    check types
make check        lint, typecheck and test, exactly what CI runs
make build        produce the deployable artifact
make clean        remove build output and caches
```

**Finish line.** A new developer runs two commands, `make install` and `make test`, and sees a passing suite. Nothing else is required.

**Edge cases most people miss.**

- The commands work only when run from the project root and fail silently elsewhere. Anchor paths inside the task file.
- `make check` and the automated pipeline drift apart, so the pipeline fails on things that passed locally. The pipeline should call `make check` and nothing else.
- The install target assumes a global tool that is not listed in the README.

---

# PART TWO. MAKING THE PROJECT REPRODUCIBLE

## Step 4. Lock every dependency, including the ones you did not choose

Loose version ranges mean the project you build today is not the project you build in six months. That is not a theoretical risk. It is the most common cause of a repository that used to work and now does not.

Do all of the following.

1. Declare direct dependencies with the version range you intend.
2. Generate a lock file that pins every dependency, direct and indirect, to an exact version with a content hash.
3. Commit the lock file.
4. Make the install command use the lock file, in the mode that fails if the lock file is out of date rather than quietly updating it.
5. Separate runtime dependencies from development dependencies so the shipped artifact does not carry test tools.
6. Turn on automated dependency update checks so the lock file moves forward deliberately, in reviewed small steps, instead of being frozen for a year and then updated in one dangerous jump.

**Finish line.** Delete your local dependency folder, run the install command, and the resulting tree is identical to the one your colleague has, byte for byte.

**Edge cases most people miss.**

- The lock file exists but the install command ignores it.
- The lock file is generated on one operating system and does not resolve on another. Generate it in the same container image the pipeline uses.
- Development and production installs share a lock file but the production path installs extras it does not need, widening the attack surface.
- A dependency is installed globally on the developer machine, so the project appears to work while the declaration is missing.

## Step 5. Make the project run without anything you own

This is the step almost every solo project fails, and it is the step that decides whether anyone else can verify your work.

If your project needs a database, a message queue, a cache or an object store, provide a way to bring all of them up locally with one command, using a container composition file. If your project calls a paid third party service, put an interface in front of that service and provide a fake implementation that returns realistic canned responses.

The test suite must run to completion with no network connection, no credentials and no accounts.

Concretely.

- Put every external system behind an interface defined in your own code, not in the vendor library's shape.
- Write one real implementation and one fake implementation of each interface.
- Choose the implementation from configuration, not from scattered conditionals in the code.
- In tests, always use the fake. In integration tests, use a real containerised database and a fake for anything that costs money or needs an account.
- Keep the fake honest. When the real service returns an error shape, the fake must be able to return that shape too, so failure paths are tested.

**Finish line.** On a machine with the network disabled and no environment file present, `make test` passes with zero skipped tests and zero errors.

**Edge cases most people miss.**

- Tests pass because missing credentials cause them to be skipped rather than to fail. Skipped tests are invisible failures. Make missing configuration a hard error in tests.
- The fake is written from the documentation rather than from a real recorded response, so it does not match reality. Record one real response, store it under `tests/fixtures`, and build the fake from it.
- Time and randomness are read directly from the system, so tests are flaky. Inject a clock and a random source, and freeze both in tests.
- Tests depend on execution order because they share state. Each test must create and destroy its own data.

## Step 6. Handle configuration and secrets properly, from the beginning

Configuration is the most common source of production incidents and it is nearly always avoidable.

The rules.

1. All configuration comes from the environment. No settings hardcoded in source. No settings read from files that differ per developer.
2. Configuration is loaded once, at startup, in one module, and validated there. Missing or malformed values cause an immediate and loud failure with a message naming the exact variable.
3. Every variable appears in `.env.example` with a safe dummy value and a one line comment explaining what it is.
4. The real environment file is in `.gitignore` and is never committed.
5. Secrets are never logged, never included in error messages, and never sent to error tracking. Mask them at the point where the configuration object is printed.
6. Sensible defaults exist for everything that is not a secret, so a developer needs to set as few things as possible.

**Finish line.** Starting the application with an empty environment produces one clear message listing every missing required variable, and the application exits. It does not start halfway and fail later.

**Edge cases most people miss.**

- Configuration validated in the web entry point but not in the background worker or the scheduled job, so those crash in production hours later.
- A variable added to the code but not to `.env.example`, so it works locally and fails on every other machine.
- Secrets appearing in a debug print of the whole settings object.
- Numeric or boolean values read as text and compared against the wrong type, so the string "false" evaluates as true.

---

# PART THREE. WRITING THE CODE

## Step 7. Build the first slice end to end before building anything wide

Do not build the database layer, then the service layer, then the interface. Build one thin path through the entire system, from the entry point to the storage and back, for the single simplest real use case. Make it work. Make it tested. Ship it.

Only then widen.

This sequencing surfaces integration problems on day two instead of week six, and it means the project always has something demonstrable.

**Finish line.** One real use case works from the outside in, has a test at every layer it touches, and is described in the README.

## Step 8. Write code that fails loudly and in one place

The single largest difference between hobby code and production code is the handling of things going wrong.

Apply these rules everywhere.

- Never catch an error only to ignore it. If you catch it, you must either handle it meaningfully, add context and re raise it, or log it at error level with enough detail to act on.
- Never catch every possible error in a broad blanket unless you are at the outermost boundary of the program. Catch the specific errors you expect.
- Define your own small set of error types for the problem domain, and translate errors from libraries into them at the adapter boundary. The domain layer should never see a database driver error.
- Validate all input at the edge of the system, once, into a typed structure. After that boundary, code trusts its own data.
- Fail fast at startup for anything that cannot recover. Fail gracefully at request time for anything that can.
- Make every failure message answer three questions. What was being attempted. What went wrong. What identifier lets someone find the rest of the story.

**Finish line.** For every place the code talks to the outside world, there is a test that makes that outside world fail and asserts the program behaves correctly.

**Edge cases most people miss.**

- Retrying an operation that is not safe to repeat, so a payment is charged twice. Only retry operations that are safe to repeat, and give write operations a unique key so repeats are detected and ignored.
- Retrying immediately in a tight loop, which turns a small outage into an outage you caused. Wait longer between each attempt, add a small random variation, and give up after a fixed number of tries.
- No timeout on a network call, so one slow dependency freezes the whole system. Every network call gets a connect timeout and a read timeout, always, with no exceptions.
- Errors swallowed inside a background task, so the task dies silently and nothing processes for days.
- Cleanup code that does not run when an error occurs. Use the language construct that guarantees cleanup.

## Step 9. Keep units small and boundaries honest

- One function does one thing. If you cannot name it without using the word "and", split it.
- Keep files under roughly four hundred lines. A file that grows past that is usually two files that have not been separated yet.
- Keep function arguments few. Past four, pass a structure.
- Prefer returning new values over changing values in place. Shared changeable state is where concurrency problems live.
- Do not reach across layers. The entry point talks to the domain. The domain talks to interfaces. Adapters implement interfaces. Nothing skips.
- Delete code you are not using. Version control remembers it. Commented out blocks are noise that hides real changes.

**Finish line.** A reviewer can read any single file in the project without needing to open three others to understand it.

## Step 10. Write the tests that actually protect you

Aim for a shape, not a number. Many fast tests of the rules, fewer tests of the wiring, a small number of tests of the whole path.

**Fast tests of the rules.** No network, no database, no filesystem. These test your domain logic. They should run in seconds and you should run them constantly while working.

**Tests of the wiring.** Real database in a container, fakes for anything external. These test that your adapters honour their interfaces and that your queries do what you think.

**Tests of the whole path.** A handful only. Start the application, drive it the way a user does, assert the result. These are slow and valuable, and too many of them make the suite unbearable.

For each piece of behaviour, write tests for four situations, not one.

1. The normal case.
2. The empty case. Zero items, empty text, missing optional value, first run with nothing stored.
3. The boundary case. One item, the maximum allowed, exactly at the limit, one over the limit.
4. The failure case. The dependency is down, the input is malformed, the operation times out, the same request arrives twice.

Additional rules that make a suite trustworthy.

- Test behaviour that matters, not internal implementation detail. A test that breaks when you rename a private function is a cost with no benefit.
- Every test must be able to run alone, and the suite must pass when run in random order.
- No test may depend on the current date, the current time zone, network availability or a live external account.
- When you fix a bug, first write the test that fails because of the bug. Then fix it. That test is now permanent protection.
- Measure how much of the code the tests exercise, publish the number, and set the required minimum just below the current level. Raise it as you go. Never lower it.

**Finish line.** You can delete any single file of production code, and at least one test fails with a message that tells you what you broke.

**Edge cases most people miss.**

- Tests that assert nothing meaningful, for example checking only that a call did not raise.
- Tests that mock the very thing they are supposed to be testing, so they always pass.
- One enormous test that covers ten behaviours, so a failure tells you nothing about which one broke.
- Test data that overlaps between tests, so passing depends on which test ran first.
- A suite that takes so long that people stop running it. Keep the fast set under a minute.

---

# PART FOUR. WORKING HABITS THAT COMPOUND

## Step 11. Commit in small pieces, each with its test

This is the habit with the highest long term return, and it is the one most often skipped.

The method.

1. Pick one behaviour to add or change. One. Not a batch.
2. Write or update the test for it first. Watch it fail for the right reason.
3. Write the smallest code that makes it pass.
4. Clean the code up while the test keeps passing.
5. Commit the test and the change together, with a message that explains why.
6. Repeat.

Message format. First line, under seventy characters, saying what changed and where, written as an instruction. Blank line. Then a short paragraph explaining why the change was needed and anything a future reader would find surprising. Reference the issue if there is one.

```
gates: reject readings below the ratchet floor

Sensor readings equal to the floor were being accepted because the
comparison used greater-or-equal. The compliance rule requires strictly
above. Adds a boundary test at the exact floor value.
```

Never do these.

- Mix an automatic reformat of many files with a real change. Do the reformat in its own commit so the real change stays readable.
- Commit generated files, build output, dependency folders or editor settings.
- Write a message that says "fix", "update", "changes" or "wip".
- Bundle three unrelated fixes into one commit because they happened on the same afternoon.
- Push a large first commit containing an entire finished project. It is worth far less than the same code arriving in forty small steps, both to reviewers and to anyone valuing the repository.

**Finish line.** Someone can read the commit list alone and understand how the project developed, in order, without opening the code.

## Step 12. Use branches and reviews even when you work alone

Working on a branch and opening a request to merge it is not ceremony. It creates a moment where you read your own work as a reviewer, and it creates a record of the reasoning.

- One branch per unit of work, named for what it does.
- Keep branches short lived. Days, not weeks. Long branches produce painful merges and hide work.
- Protect the main branch so nothing merges without the automated checks passing.
- In the merge request description, write what changed, why, how you tested it, and what you deliberately did not do.
- Review your own request before asking anyone else. Read every line as if a stranger wrote it.

**Finish line.** The main branch is always releasable, and every change on it arrived through a request with passing checks.

## Step 13. Record decisions when you make them, in five lines

Every project accumulates decisions that later look arbitrary. Write each one down when you make it, while the reasoning is fresh. Use one small file per decision in `docs/decisions`, numbered in order.

Five headings, a few sentences each. Context. Decision. Alternatives considered. Consequences. Status.

This costs five minutes and it prevents the most expensive kind of rework, which is undoing a deliberate decision that nobody remembered was deliberate.

**Finish line.** Every non obvious choice in the codebase can be traced to a written reason.

---

# PART FIVE. AUTOMATION THAT ENFORCES RATHER THAN REPORTS

## Step 14. Make the machine check what humans forget

Set up automated checks that run on every push and every merge request, and configure them to block the merge when they fail. A check that reports but does not block is decoration.

The pipeline, in order, failing fast.

1. Install from the lock file, with caching so it is quick.
2. Format check. The code matches the formatter output exactly.
3. Lint. No unused imports, no shadowed names, no obvious mistakes.
4. Type check. If the language has types, they are checked, and the strictness level is recorded and raised over time.
5. Fast tests.
6. Slower tests with a real containerised database.
7. Coverage check against the recorded minimum.
8. Dependency vulnerability audit.
9. Secret scan of the whole history, not just the change.
10. Build the artifact or container image.
11. Start the built artifact and call its health endpoint, proving it actually runs.

Also set up.

- The same checks available locally through `make check`, so nobody discovers a failure only after pushing.
- Automated hooks that run the formatter and the fast checks before a commit is created.
- A scheduled run of the full pipeline once a week against the main branch, which catches breakage caused by the outside world rather than by your changes.

**Finish line.** A change that breaks style, types, tests, coverage or security cannot reach the main branch, and the person who wrote it learns this within a few minutes.

**Edge cases most people miss.**

- The pipeline caches so aggressively that it stops testing a genuine fresh install. Run one uncached install on a schedule.
- The pipeline runs only on one operating system and one runtime version while the product supports several.
- The build succeeds but nobody ever starts the built artifact, so an image that cannot boot ships happily.
- Checks that can be skipped with a commit message flag, which becomes routine.

## Step 15. Make the application observable before you need to observe it

You cannot debug production with print statements. Put this in place while the system is small.

- **Structured logs.** One line per event, in machine readable form, with consistent field names. Always include a request or job identifier that ties related lines together. Log at the boundaries, on state changes and on failures. Do not log inside tight loops.
- **Levels used honestly.** Debug for development detail. Info for meaningful business events. Warning for something recovered from. Error for something a human must look at. Never use error for expected conditions, or the level becomes meaningless.
- **A health endpoint** that reports whether the process is alive, and a separate readiness endpoint that reports whether it can actually serve, meaning its dependencies answer.
- **Basic measurements.** How many requests or jobs, how long they took, how many failed. Three numbers cover most questions.
- **Error tracking** that captures the failure, the stack, and the surrounding context, with secrets removed.
- **A correlation identifier** generated at the entry point, attached to every log line, and passed to every downstream call.

**Finish line.** Given only a user report saying "it failed around three o'clock", you can find the exact failure, its cause and its context, in under five minutes, without adding new code.

**Edge cases most people miss.**

- Logging the whole request body, including personal data or passwords.
- Logs written to a local file inside a container, where nothing collects them and they vanish on restart.
- Health endpoint that returns healthy while the database connection is dead, because it only checks that the process is running.
- Timestamps recorded in local time without a zone, making correlation across machines impossible. Record in coordinated universal time with an explicit zone marker.

## Step 16. Handle data changes as carefully as code changes

If the project stores anything, this applies.

- Every change to the storage shape is a numbered migration file, committed with the code that needs it.
- Migrations run forward automatically on deploy, and each one has a tested way back.
- Never change a column and its usage in the same release. Add the new shape, write to both, move readers over, then remove the old shape in a later release. This is what allows a deploy to be rolled back safely.
- Have a backup, and restore from it into a scratch environment on a schedule. A backup nobody has restored is not a backup.
- Keep a small realistic seed data set committed, so any developer gets a usable local system in one command.
- Know and write down what personal data is stored, where, for how long, and how it is deleted on request.

**Finish line.** You can create a fresh database, run every migration from empty to current, load seed data and start the application, all with one command.

## Step 17. Do the security basics, because they are basics

- Never commit secrets. Scan the entire history for them once, and scan every change from then on. If a secret was ever committed, rotate it. Removing it from the history does not un leak it.
- Validate and constrain every input from outside. Length, type, range, allowed values.
- Use parameterised queries. Never build a query by joining strings.
- Escape output according to where it is going.
- Keep authentication and permission checks in one place, and check permissions on every path, including the ones you think are internal.
- Store passwords with a purpose built password hashing algorithm, never a general purpose hash.
- Set the standard protective response headers on web responses.
- Rate limit anything a stranger can call.
- Run the application as a non privileged user, in a minimal container image, with a fixed base image version rather than a moving tag.
- Audit dependencies for known vulnerabilities in the pipeline, and treat a high severity finding as a build failure.

**Finish line.** A security review finds nothing in the standard list, because the standard list is automated.

---

# PART SIX. DOCUMENTATION, RELEASE AND CONTINUITY

## Step 18. Write the documentation a stranger needs, not the documentation you enjoy writing

The README is the front door. It has a fixed job and a fixed shape.

1. One paragraph. What this is and who it is for.
2. Requirements. Exact versions of what must be installed first.
3. Install. The exact commands, in order, copy and paste ready.
4. Run. How to start it locally and what you should see.
5. Test. The one command, and what a pass looks like.
6. Configuration. Every variable, what it does, whether it is required, and its default.
7. Architecture. A short description of the main parts and how a request or job flows through them.
8. Troubleshooting. The three problems people actually hit, and their fixes.
9. Contributing. A link to the contributing file.
10. Licence.

Beyond the README, keep `docs/architecture.md` for the shape of the system, `CHANGELOG.md` for what changed in each version, `CONTRIBUTING.md` for how to work on it, and the decisions folder for why things are the way they are.

Write comments only where the code cannot explain itself. Comment the reason, never the mechanics. A comment saying what the next line does is a comment that will be wrong within a month.

**Finish line.** Hand the repository to someone unfamiliar with it, say nothing, and they have it running and its tests passing within fifteen minutes.

**Edge cases most people miss.**

- The README describes the intended state rather than the current one.
- Install instructions that were correct once and were never rerun on a clean machine. Reverify them whenever they change and once per release.
- Architecture documentation with no diagram, where a diagram would have replaced two pages.
- A contributing file that lists rules the automated checks do not enforce, so nobody follows them.

## Step 19. Release deliberately and describe what changed

- Use a three part version number. Increase the first part for breaking changes, the second for new features, the third for fixes.
- Tag the exact commit that was released.
- Keep the changelog grouped under Added, Changed, Fixed, Removed and Security, written for the person using the software rather than the person who wrote it.
- Build the artifact once and promote that same artifact through environments. Never rebuild for production, because then production runs something that was never tested.
- Know how to roll back, write it down, and try it at least once before you need it.

**Finish line.** Any released version can be identified, rebuilt, inspected and rolled back to, months later.

## Step 20. Keep the project alive in small regular increments

A repository that stops moving loses value quickly, in credibility, in security posture and in commercial terms.

The weekly rhythm.

- Land several small changes, each with tests, spread across days rather than compressed into one sitting.
- Merge the dependency update requests, or record why you are not.
- Look at the error tracking and fix or explicitly accept the top item.
- Delete something. Dead code, an unused setting, an obsolete document.

The monthly rhythm.

- Do the clean clone check on a fresh machine or a clean container, following only the README.
- Restore a backup into a scratch environment.
- Read the oldest open issue and either act on it or close it.
- Raise one automated check by one notch. Coverage minimum, type strictness or lint rule set.

**Finish line.** The activity history shows continuous work over months, by whoever really did it, in changes small enough to read.

---

# PART SEVEN. THE FAILURES THAT AUTOMATED CODING ASSISTANTS PRODUCE MOST OFTEN

These are the specific gaps that appear when code is generated quickly and looks complete. Check each one explicitly, because none of them make the code fail on the happy path, which is the only path that usually gets tried.

**Correctness and edges**

1. Only the normal case is handled. No empty input, no single item, no maximum size, no duplicate submission.
2. Off by one at boundaries. Inclusive and exclusive limits confused in ranges, pagination and slicing.
3. Number handling for money using floating point, which loses precision. Use a decimal type or integer smallest units.
4. Date handling without time zones, or comparing a zone aware value with a naive one.
5. Text handling that assumes one byte per character, breaking on accents, other scripts and emoji, and breaking length limits.
6. Sorting that depends on the language settings of the machine.
7. Comparing values of different types and getting a silent wrong answer instead of an error.

**Concurrency and ordering**

8. Read then write without protection, so two simultaneous operations overwrite each other. Use a transaction or a conditional update.
9. Assuming events arrive in order or exactly once. Design so repeats are harmless and order is not assumed.
10. Shared changeable state across requests or threads.
11. Background tasks started without any way to wait for them, cancel them or notice when they die.

**Resources**

12. Files, connections and network sockets opened and never closed on the error path.
13. Loading an entire file or an entire query result into memory. Stream and paginate.
14. Connection pools created per request instead of once per process.
15. A query inside a loop, producing hundreds of round trips where one query would do.
16. No limit on how much a caller can request, so one request can exhaust the machine.

**Dependencies and the outside world**

17. No timeout anywhere on network calls.
18. Retry logic that repeats unsafe operations, or retries immediately without waiting.
19. No handling for the dependency returning a valid response with an unexpected shape.
20. Trusting the status code without checking the body, or the reverse.
21. No fallback or clear failure when a non essential dependency is unavailable.

**Tests**

22. Tests that mock everything and therefore prove nothing.
23. Tests that pass by being skipped when configuration is missing.
24. Tests that require the internet, an account or the current date.
25. No test for the failure path, which is the path that will actually cause an incident.
26. Fixtures shared and mutated between tests.

**Structure and maintenance**

27. Configuration values written directly into the code.
28. Business rules mixed into the database and network layer, so nothing can be tested quickly.
29. One file growing to thousands of lines because generation kept appending.
30. Duplicate near identical functions created rather than one function extended.
31. Dependencies added for a single small function.
32. Generated code committed without the generator, or with the generator not recorded.

**Delivery**

33. No lock file.
34. Container images built on a moving base tag, so the image changes without the code changing.
35. Migrations that cannot be reversed, applied automatically.
36. Health checks that report healthy regardless of the state of dependencies.
37. Secrets in build logs, error messages or client side code.
38. Everything committed in one enormous first commit with no history.

---

# PART EIGHT. THE DEFINITION OF DONE

A change is done when every line below is true. Not before.

**The change itself**

- [ ] It does one thing, and the commit message says why in a sentence a stranger understands.
- [ ] Normal, empty, boundary and failure cases are handled.
- [ ] Errors are specific, contextual and either handled or raised with information added.
- [ ] Nothing secret is logged, printed or returned.
- [ ] No new configuration exists without an entry in the example environment file and the README.

**Tests**

- [ ] A test exists that fails without this change.
- [ ] Failure paths are tested, not only success paths.
- [ ] The full suite passes from a clean checkout with no network and no credentials.
- [ ] The suite passes in random order.
- [ ] Coverage did not go down.

**Repository**

- [ ] The lock file is committed and current.
- [ ] Format, lint and type checks pass locally through the single check command.
- [ ] The README still matches reality.
- [ ] The changelog has an entry if a user would notice this.
- [ ] A decision file exists if a real choice was made.

**Operations**

- [ ] Logs make this change traceable in production.
- [ ] Any migration has a tested way back.
- [ ] The artifact builds, starts and answers its health check.
- [ ] Rolling back this change is possible and understood.

---

# PART NINE. THE PROMPT

Paste the block below at the start of any session with an automated coding assistant, before describing the task. It converts the assistant from a producer of plausible code into a producer of verifiable code.

```
You are writing production code that a stranger must be able to clone, install,
run and verify on a clean machine with no network access and no accounts. Work
to the following standard on every task, without me repeating it.

STRUCTURE
Separate business rules from anything that touches the network, the database or
the filesystem. Business rules must be testable without any of those. Put every
external service behind an interface defined in our code, with a real
implementation and a fake implementation.

CONFIGURATION
No hardcoded settings. Load all configuration from the environment in one place,
validate it at startup, fail immediately and loudly with the exact names of any
missing variables. Add every new variable to the example environment file and
the README in the same change. Never log secrets.

ERRORS
Catch specific errors, never blanket ones except at the outermost boundary.
Never swallow an error. Add context and re raise, or handle it meaningfully.
Translate library errors into our own error types at the boundary. Every network
call gets a connect timeout and a read timeout. Only retry operations that are
safe to repeat, wait longer between attempts, and give up after a fixed count.
Guarantee cleanup of files, connections and locks on the error path.

TESTS
Every change arrives with tests. For each behaviour, cover four cases: normal,
empty, boundary and failure. Tests must run with no network, no credentials and
no external accounts, and must never be skipped for missing configuration.
Freeze time and randomness. No shared mutable fixtures. No test may depend on
another test or on execution order. Do not mock the thing under test.

EDGES YOU MUST HANDLE WITHOUT BEING ASKED
Empty and single item inputs. Exact boundary values and one past them. Duplicate
and repeated requests. Simultaneous writes to the same record. Text with accents
and other scripts. Money as decimal or integer units, never floating point.
Dates with explicit time zones, stored in coordinated universal time. Large
inputs handled by streaming or pagination rather than loading everything.
Explicit limits on how much a caller can request.

REPOSITORY HYGIENE
Commit a dependency lock file and use it for installs. Provide one command each
for install, test, lint, type check and run, and make the automated pipeline
call exactly those commands. Keep files under about four hundred lines. Delete
dead code rather than commenting it out. Do not commit generated output.

HOW TO DELIVER
Work in small steps. For each step, state what you are about to do, write the
test first, then the smallest implementation, then show me both. One logical
change per commit, with a message that explains why. Do not produce one large
finished dump of files.

BEFORE YOU SAY IT IS DONE
List, honestly and specifically: what you did not implement, what you assumed,
which edge cases are still unhandled, and what would break first under load or
under a failing dependency. If something cannot be tested without an account,
say so plainly rather than writing a test that quietly skips.
```

---

# APPENDIX. THE DAILY LOOP

Print this. It is the whole method in one page.

1. Pull the latest main branch. Run the check command. Confirm it is green before you change anything.
2. Create a branch named for the one thing you are about to do.
3. Write the failing test.
4. Write the smallest code that passes it.
5. Tidy up while the test stays green.
6. Run the full check command.
7. Commit the test and the code together, explaining why.
8. Repeat steps three to seven for the next small piece.
9. Open the merge request. Read your own change as a stranger would. Fix what you find.
10. Merge only when every automated check is green.
11. Update the README, the changelog or a decision file if this change made any of them out of date.
12. Tomorrow, do it again. The value is in the repetition, not in any single day.
