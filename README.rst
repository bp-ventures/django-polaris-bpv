==================
Django Polaris BPV
==================

*A modernised, actively maintained fork of* `django-polaris <https://github.com/stellar/django-polaris>`_ *from the Stellar Development Foundation.*

|build| |coverage| |pypi| |python| |django| |license|

.. |build| image:: https://img.shields.io/github/actions/workflow/status/bp-ventures/django-polaris-bpv/test.yml?branch=master&style=flat-square
    :alt: Build
    :target: https://github.com/bp-ventures/django-polaris-bpv/actions

.. |coverage| image:: https://img.shields.io/codecov/c/github/bp-ventures/django-polaris-bpv?style=flat-square
    :alt: Coverage
    :target: https://codecov.io/gh/bp-ventures/django-polaris-bpv

.. |pypi| image:: https://img.shields.io/pypi/v/django-polaris-bpv?style=flat-square
    :alt: PyPI
    :target: https://pypi.python.org/pypi/django-polaris-bpv

.. |python| image:: https://img.shields.io/badge/python-3.12%20%7C%203.13-blue?style=flat-square
    :alt: Python
    :target: https://pypi.python.org/pypi/django-polaris-bpv

.. |django| image:: https://img.shields.io/badge/django-%3E%3D%206.0-0C4B33?style=flat-square
    :alt: Django
    :target: https://pypi.python.org/pypi/django-polaris-bpv

.. |license| image:: https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square
    :alt: License
    :target: https://github.com/bp-ventures/django-polaris-bpv/blob/master/LICENSE

----

Why This Fork?
--------------

The upstream `django-polaris`_ (v2.6.0) targets Python 3.10+ and Django 4.2.
This fork brings the stack up to date:

.. list-table::
   :header-rows: 1
   :widths: 25 35 35

   * -
     - Upstream (stellar)
     - **BPV Fork**
   * - Python
     - >= 3.10
     - **>= 3.12**
   * - Django
     - >= 4.2
     - **>= 6.0**
   * - Django REST Framework
     - >= 3.12
     - **>= 3.16**
   * - stellar-sdk
     - >= 10
     - **>= 10** *(aiohttp async transport)*
   * - pytz
     - required
     - **removed** *(stdlib zoneinfo)*

**Python 3.12+**
    ~25 % average speed-up over 3.10 thanks to the Faster CPython project
    (PEP 659 adaptive interpreter, inlined calls, optimised comprehensions).

**Django 6.0**
    Latest release track with improved async views and native ``zoneinfo`` support.

**stellar-sdk >= 10 with aiohttp**
    Async-first Stellar SDK, tested against current v13.x releases.

**No pytz**
    Replaced by the stdlib ``zoneinfo`` module — one fewer dependency to manage.

----

Quick Start
-----------

.. code-block:: bash

   pip install django-polaris-bpv

Add to ``INSTALLED_APPS``:

.. code-block:: python

   INSTALLED_APPS = [
       ...,
       "polaris",
   ]

See the full `documentation`_ for integration details, or explore the
`reference server`_ with the `demo wallet`_.

----

About Polaris
-------------

Polaris is an extendable `django app`_ for Stellar Ecosystem Proposal (SEP)
implementations. Using Polaris, you can run a web server supporting any
combination of SEP-1, 6, 10, 12, 24, 31, & 38.

----

About BP Ventures
-----------------

Since 2020, `BP Ventures`_ has helped fintechs, banks, and charities with
comprehensive blockchain and payment solutions:

- **Anchor in the Box** — Join the Stellar Ecosystem
- **White label wallet** — No gas fee transfers
- **Guard / SEP-30** — Key security solutions

Focus on serving your clients while we handle all your blockchain infrastructure needs.

----

.. _django-polaris: https://github.com/stellar/django-polaris
.. _django app: https://docs.djangoproject.com/en/stable/intro/reusable-apps/
.. _`demo wallet`: http://demo-wallet.stellar.org
.. _`reference server`: https://testanchor.stellar.org/.well-known/stellar.toml
.. _`documentation`: https://django-polaris-bpv.readthedocs.io/
.. _`BP Ventures`: https://www.bpventures.us/
