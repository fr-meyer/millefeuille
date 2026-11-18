# External wire identity versus product identity

The repository hard-rename guard remains mandatory. The external OpenKB v0.1 source-pack schema identifier is a wire-format compatibility requirement, not a package, CLI or product name. The guard exempts only the exact double-quoted schema literal in `millefeuille/domain/openkb_import.py`, its synthetic test `tests/test_openkb_import.py`, and the guard declaration in `tests/test_millefeuille_hard_rename.py`. No whole file is excluded.

The literal stays unchanged in the importer: it is not reconstructed or disguised to evade checking. Other legacy markers in those same files, the literal in another file, and other schema versions still fail the guard. Negative boundary tests preserve this distinction. Publication, source import approval, and runtime execution policy are unchanged.
