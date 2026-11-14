"""Real-data ingestion and synthetic enrollment generation for Hexam.

Pipeline (each step is a CLI module, run from ``backend/``)::

    python -m hexam.data_generation.fetch_sources --term 202608   # download + cache public sources
    python -m hexam.data_generation.normalize --term 202608       # real courses/sections/exam groups/slots + baseline
    python -m hexam.data_generation.generate --num-students 1000 --seed 42
    python -m hexam.data_generation.validate                      # integrity + reproducibility checks
    python -m hexam.data_generation.stats                         # dataset statistics

or all at once: ``python -m hexam.data_generation.build``.

Data categories are kept strictly apart (see ``Provenance.source``):

* ``gt_public``  — parsed from a cached public Georgia Tech source (GT Scheduler
  crawler of OSCAR, the registrar's final exam matrix, IRP enrollment tables).
* ``assumed``    — documented modelling assumptions (curriculum weights, course-pair
  affinities, majors without public headcounts, synthetic section capacities).
* ``synthetic``  — generator output (every student and every registration).
"""
