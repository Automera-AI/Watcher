"""The clinic vertical's data intake (demo step 4).

One module: :mod:`apps.api.clinic.importer`, which turns the client's availability workbook into
the validated records ``db/clinic_repo.py`` persists. The reading of the ``.xlsx`` itself lives in
``scripts/import_clinic_workbook.py`` — ``openpyxl`` is an operator's dependency, not the
application's, exactly as it is for ``scripts/import_property_facts.py``. Everything that decides
whether the data is fit to import is here, where it is tested.
"""
