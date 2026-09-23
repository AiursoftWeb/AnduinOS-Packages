# Python migration reference

This is the previous voice backend, retained only for reproducible migration
comparisons. It is not included in the deb, D-Bus activation, or normal source
import path. Production capture, scheduling, calibration and recognition live
in Rust; the Python GTK frontend imports only `src/anduinos_whisper_framework`.

Legacy Python unit/benchmark runners explicitly add this directory to their
import path. Shared frontend helpers are resolved from `src` by the reference
package initializer, without duplicating the settings/schema implementation.
`python-service` is used only by the private-bus idle-overhead comparison.

Passing a reference test is not Rust coverage. Rust native corpus tests run both
implementations against the same public PCM and compare their actual outputs.
The headless desktop test runs the Rust fixture service, not this reference.
