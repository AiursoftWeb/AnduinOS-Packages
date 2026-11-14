# Develop the Apkg Platform

This chapter is for changes to Apkg's SDK, CLI, and web server. To develop an application packaged by Apkg, use [Develop AnduinOS packages](Develop-AnduinOS-Packages.md).

## Build and test locally

Use the [Apkg source repository](https://github.com/AiursoftWeb/Apkg), .NET 10 SDK, Node.js, and the external tools needed by the tests you select. The web frontend's dependencies live under `src/Aiursoft.Apkg/wwwroot`.

```bash
dotnet restore --no-cache --configfile nuget.config
dotnet build -maxcpucount:1 --no-self-contained
dotnet test Aiursoft.Apkg.sln
```

The checked-in NuGet configuration supports external contributors; CI can inject its internal mirror. Avoid committing an environment-specific feed or credential as part of an unrelated change.

Run the repository's `lint.sh` for its security-baseline checks and ReSharper inspections. It can install the JetBrains global tool when missing, restore dependencies, and write an inspection report. Its known inspection filters include `InconsistentNaming`, `AssignNullToNotNullAttribute`, `UnusedAutoPropertyAccessor`, `DuplicateResource`, and `NotOverriddenInSpecificCulture`. Review the actual script and report when diagnosing lint, rather than treating every filtered warning as a universally harmless pattern.

## CI stages

The reviewed pipeline has `build`, `validate`, `publish`, and `deploy` stages. It restores without cache, builds with one MSBuild worker, runs lint and tests, validates Docker architectures and applicable `.aosproj` projects, packs NuGet artifacts, and deploys through configured jobs. Coverage uses Cobertura with reportgenerator, and tests produce JUnit reports.

Master deployment includes NuGet and multi-architecture container publication as configured by the pipeline. Build validation is not authorization to publish a developer's local changes. Follow the repository's contribution and release process.

Docker's frontend layer copies `package.json` and its lockfile before the full source so npm dependencies can be cached across C# changes. A frontend dependency change invalidates that layer. Check the final frontend build, not just a cached `npm install`.

## Test isolation and assertion APIs

The suite uses MSTest and a web `TestBase` with HTTP/TestServer helpers, administrator login, and form posting. Each test should have its own client so authentication cookies cannot leak between cases.

Older notes described missing `Assert.ThrowsExceptionAsync`. Do not interpret that as "MSTest has no asynchronous exception assertions": current tests use `Assert.ThrowsAsync`. Match the installed test framework. A manual fallback remains:

```csharp
try
{
    await SomeOperationAsync();
    Assert.Fail("Expected InvalidOperationException was not thrown.");
}
catch (InvalidOperationException)
{
    // Expected failure.
}
```

Keep the expected exception specific, and avoid catching assertion failures as the expected result.

## Real database coverage

The startup path selects InMemory when `EntryExtends.IsInUnitTests()` is true. InMemory does not enforce relational foreign keys and does not reproduce SQLite locking or MySQL DDL behavior. Passing those tests does not validate every database provider.

For storage/model changes, test both real SQLite and MySQL configurations. Generate migrations for both providers and inspect their SQL and snapshots:

```bash
dotnet ef migrations add YourMigrationName \
  --project src/Aiursoft.Apkg.MySql --startup-project src/Aiursoft.Apkg
dotnet ef migrations add YourMigrationName \
  --project src/Aiursoft.Apkg.Sqlite --startup-project src/Aiursoft.Apkg
```

Use the EF tool version appropriate to the repository. Include both provider migrations in the same model change. An InMemory test that constructs a dangling FK can be useful for defensive logic while representing a state a real relational engine would reject during insertion.

`ApkgDbContext.MigrateAsync` configures a ten-minute command timeout. Large MySQL column changes can rebuild tables and exceed it. Plan large data changes as staged schema/backfill work instead of assuming a longer online timeout is an adequate migration design.

## Common engineering pitfalls

| Symptom | Investigation/action |
|---------|----------------------|
| `MSB3492` or locked build output | Check concurrent builds; the pipeline uses `-maxcpucount:1` and disables node reuse. Clean only the affected generated output when necessary |
| Unexpected dependency version | Inspect feeds and locked versions, then try `dotnet restore --no-cache` |
| POST data lost after redirect | Check `/Controller` versus `/Controller/Action`; a 302 can turn a POST into GET |
| Permission tests pass only in a certain order | Look for shared cookies/HTTP clients or state leakage |
| Disposable HTTP content fails during initialization | Make stream/content ownership and exception cleanup explicit; use a factory with well-defined disposal behavior rather than a complex initializer |
| Razor editor cannot resolve a section | Check UiStack's dynamic layout; a narrowly scoped suppression may be appropriate |

The stream-content lesson is about ownership during exceptions: property initialization can fail before the intended `using` scope is established. Do not assume an initializer automatically disposes every object it created. If using a factory, ensure that failure inside the factory also disposes resources it owns.

For a known UiStack section false positive, the repository uses:

```cshtml
@* ReSharper disable once Razor.SectionNotResolved *@
@section Scripts {
    // Page-specific content.
}
```

Keep the suppression local and preserve the layout's actual section name/casing. The historical count of suppressions is not a required number to maintain.

## Invariants to protect during changes

Read [the architecture](Design-and-Architecture.md) before changing bucket creation, GC, signing, local override resolution, or pool paths. Run the relevant atomic-bucket, GC/sign-race, signing, local-package-sync, and upload-conflict tests. For builder changes, cover repeat-build hashes, target-specific payloads, upstream verification, and actual control archives.

Queue scheduling is relative to startup. Inspect `Startup.cs` rather than old illustrative timeline comments; the current table is in [server configuration](Server-Configuration.md#scheduled-jobs). Public endpoints, database state, and job evidence should all agree before declaring a publication bug fixed.

## Documentation ownership

All maintained Apkg guides and design references live in the **Apkg** section of AnduinOS Documentation. Update the appropriate page there when changing a command, XML field, API contract, or server invariant. Source code may link to those pages, but should not recreate a second divergent documentation tree inside the Apkg repository.
