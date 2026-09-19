# Install the Apkg CLI

Install the CLI on the machine that builds packages. You do not need to install the web server first. These instructions target Linux development hosts with Debian package tools.

## Check for an existing installation

```bash title="Inspect the installed command"
command -v apkg
apkg --version
```

If these commands work, continue to [your first package](Build-Your-First-Package.md). To investigate conflicting installations, run `type -a apkg`; the first path is the command your shell will execute.

## Option 1: AnduinOS package

On AnduinOS with an enabled repository that provides the `apkg` package:

```bash title="Check availability and install"
sudo apt update
apt-cache policy apkg
sudo apt install apkg
apkg --version
```

Check that `apt-cache policy` reports a candidate first. The AnduinOS recipe targets `resolute-addon`; do not add that suite to an older Ubuntu installation merely to obtain this tool. Use the .NET tool route below if your configured sources do not provide it.

The distribution package manages the launcher and runtime dependencies through APT. Update it through APT as well:

```bash
sudo apt install --only-upgrade apkg
```

## Option 2: .NET global tool

Apkg CLI `10.0.55` targets .NET 10. Install the .NET 10 SDK using the instructions for your Ubuntu base release in the [official .NET installation guide](https://learn.microsoft.com/en-us/dotnet/core/install/linux-ubuntu-install). On a release whose configured repositories provide it, the package command is:

```bash title="Install the development prerequisites"
sudo apt update
sudo apt install dotnet-sdk-10.0 dpkg-dev
dotnet --list-sdks
dotnet --list-runtimes
```

The SDK includes the tooling used to install global tools. Check that the required .NET and ASP.NET Core 10 runtimes are present; a runtime error will identify the missing framework. Build-time requirements for your application, such as Rust, GCC, or gettext, are separate.

Install as your regular development user:

```bash title="Install Apkg"
dotnet tool install --global Aiursoft.Apkg.Client
export PATH="$PATH:$HOME/.dotnet/tools"
apkg --version
apkg --help
```

If necessary, add the `export PATH` line to your shell profile so new terminals can find the tool. For a build environment that needs a reproducible CLI version, use `--version 10.0.55` on the install command and update that pin deliberately.

Update or uninstall this installation using the same user:

```bash
dotnet tool update --global Aiursoft.Apkg.Client
# To remove the global tool:
dotnet tool uninstall --global Aiursoft.Apkg.Client
```

!!! note "Package building and administrator privileges"

    Run `apkg new`, `lint`, `build`, and `publish` as your regular user. Installing a `.deb` or changing APT sources needs administrator privileges. A per-user .NET tool may not be on `sudo`'s PATH; you can always install the generated `.deb` with `sudo apt install ./path/to/package.deb`.

## Know the commands

| Command | Result |
|---------|--------|
| `apkg new --name hello-apkg` | Create a recipe in the current directory |
| `apkg add ./assets/hello-apkg --target /usr/bin/hello-apkg --mode 755` | Add a file mapping to the current recipe |
| `apkg lint` | Validate the recipe |
| `apkg build` | Generate `.deb` files in `bin/` |
| `apkg publish` | Build and bundle packages into a local `.apkg` archive |
| `apkg push --file ./bin/example.apkg --source https://apkg.example.com --api-key "$APKG_API_KEY"` | Upload a bundle |
| `apkg deploy --source https://apkg.example.com --api-key "$APKG_API_KEY" --skip-existing` | Check destination versions, then build and upload when needed |

Run `apkg COMMAND --help` for the installed release's options. In particular, `new` defaults to the current directory, `add` takes a positional source path, and `push` takes `--file`.

Next: [Build your first Debian package](Build-Your-First-Package.md).
