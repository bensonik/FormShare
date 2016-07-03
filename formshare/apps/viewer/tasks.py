import re
import sys
from celery import task
from django.db import transaction
from django.conf import settings
from django.core.mail import mail_admins
from requests import ConnectionError

from formshare.apps.viewer.models.export import Export
from formshare.libs.exceptions import NoRecordsFoundError
from formshare.libs.utils.export_tools import generate_export,\
    generate_attachments_zip_export, generate_kml_export,\
    generate_external_export
from formshare.libs.utils.logger_tools import mongo_sync_status, report_exception


def create_async_export(xform, export_type, query, force_xlsx, options=None):
    username = xform.user.username
    id_string = xform.id_string

    @transaction.commit_on_success
    def _create_export(xform, export_type):
        return Export.objects.create(xform=xform, export_type=export_type)

    export = _create_export(xform, export_type)
    result = None
    arguments = {
        'username': username,
        'id_string': id_string,
        'export_id': export.id,
        'query': query,
    }
    if export_type in [Export.XLS_EXPORT, Export.GDOC_EXPORT,
                       Export.CSV_EXPORT, Export.CSV_ZIP_EXPORT,
                       Export.SAV_ZIP_EXPORT]:
        if options and "group_delimiter" in options:
            arguments["group_delimiter"] = options["group_delimiter"]
        if options and "split_select_multiples" in options:
            arguments["split_select_multiples"] =\
                options["split_select_multiples"]
        if options and "binary_select_multiples" in options:
            arguments["binary_select_multiples"] =\
                options["binary_select_multiples"]

        # start async export
        if export_type in [Export.XLS_EXPORT, Export.GDOC_EXPORT]:
            result = create_xls_export.apply_async((), arguments, countdown=10)
        elif export_type == Export.CSV_EXPORT:
            result = create_csv_export.apply_async(
                (), arguments, countdown=10)
        elif export_type == Export.CSV_ZIP_EXPORT:
            result = create_csv_zip_export.apply_async(
                (), arguments, countdown=10)
        elif export_type == Export.SAV_ZIP_EXPORT:
            result = create_sav_zip_export.apply_async(
                (), arguments, countdown=10)
        else:
            raise Export.ExportTypeError
    elif export_type == Export.ZIP_EXPORT:
        # start async export
        result = create_zip_export.apply_async(
            (), arguments, countdown=10)
    elif export_type == Export.KML_EXPORT:
        # start async export
        result = create_kml_export.apply_async(
            (), arguments, countdown=10)
    elif export_type == Export.EXTERNAL_EXPORT:
        if options and "token" in options:
            arguments["token"] = options["token"]
        if options and "meta" in options:
            arguments["meta"] = options["meta"]

        result = create_external_export.apply_async(
            (), arguments, countdown=10)
    else:
        raise Export.ExportTypeError
    if result:
        # when celery is running eager, the export has been generated by the
        # time we get here so lets retrieve the export object a fresh before we
        # save
        if settings.CELERY_ALWAYS_EAGER:
            export = Export.objects.get(id=export.id)
        export.task_id = result.task_id
        export.save()
        return export, result
    return None


@task()
def create_xls_export(username, id_string, export_id, query=None,
                      force_xlsx=True, group_delimiter='/',
                      split_select_multiples=True,
                      binary_select_multiples=False):
    # we re-query the db instead of passing model objects according to
    # http://docs.celeryproject.org/en/latest/userguide/tasks.html#state
    ext = 'xls' if not force_xlsx else 'xlsx'

    try:
        export = Export.objects.get(id=export_id)
    except Export.DoesNotExist:
        # no export for this ID return None.
        return None

    # though export is not available when for has 0 submissions, we
    # catch this since it potentially stops celery
    try:
        gen_export = generate_export(
            Export.XLS_EXPORT, ext, username, id_string, export_id, query,
            group_delimiter, split_select_multiples, binary_select_multiples)
    except (Exception, NoRecordsFoundError) as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("XLS Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e, sys.exc_info())
        # Raise for now to let celery know we failed
        # - doesnt seem to break celery`
        raise
    else:
        return gen_export.id


@task()
def create_csv_export(username, id_string, export_id, query=None,
                      group_delimiter='/', split_select_multiples=True,
                      binary_select_multiples=False):
    # we re-query the db instead of passing model objects according to
    # http://docs.celeryproject.org/en/latest/userguide/tasks.html#state
    export = Export.objects.get(id=export_id)
    try:
        # though export is not available when for has 0 submissions, we
        # catch this since it potentially stops celery
        gen_export = generate_export(
            Export.CSV_EXPORT, 'csv', username, id_string, export_id, query,
            group_delimiter, split_select_multiples, binary_select_multiples)
    except NoRecordsFoundError:
        # not much we can do but we don't want to report this as the user
        # should not even be on this page if the survey has no records
        export.internal_status = Export.FAILED
        export.save()
    except Exception as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("CSV Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e, sys.exc_info())
        raise
    else:
        return gen_export.id


@task()
def create_kml_export(username, id_string, export_id, query=None):
    # we re-query the db instead of passing model objects according to
    # http://docs.celeryproject.org/en/latest/userguide/tasks.html#state

    export = Export.objects.get(id=export_id)
    try:
        # though export is not available when for has 0 submissions, we
        # catch this since it potentially stops celery
        gen_export = generate_kml_export(
            Export.KML_EXPORT, 'kml', username, id_string, export_id, query)
    except (Exception, NoRecordsFoundError) as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("KML Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e, sys.exc_info())
        raise
    else:
        return gen_export.id


@task()
def create_zip_export(username, id_string, export_id, query=None):
    export = Export.objects.get(id=export_id)
    try:
        gen_export = generate_attachments_zip_export(
            Export.ZIP_EXPORT, 'zip', username, id_string, export_id, query)
    except (Exception, NoRecordsFoundError) as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("Zip Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e)
        raise
    else:
        if not settings.TESTING_MODE:
            delete_export.apply_async(
                (), {'export_id': gen_export.id},
                countdown=settings.ZIP_EXPORT_COUNTDOWN)
        return gen_export.id


@task()
def create_csv_zip_export(username, id_string, export_id, query=None,
                          group_delimiter='/', split_select_multiples=True,
                          binary_select_multiples=False):
    export = Export.objects.get(id=export_id)
    try:
        # though export is not available when for has 0 submissions, we
        # catch this since it potentially stops celery
        gen_export = generate_export(
            Export.CSV_ZIP_EXPORT, 'zip', username, id_string, export_id,
            query, group_delimiter, split_select_multiples,
            binary_select_multiples)
    except (Exception, NoRecordsFoundError) as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("CSV ZIP Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e, sys.exc_info())
        raise
    else:
        return gen_export.id


@task()
def create_sav_zip_export(username, id_string, export_id, query=None,
                          group_delimiter='/', split_select_multiples=True,
                          binary_select_multiples=False):
    export = Export.objects.get(id=export_id)
    try:
        # though export is not available when for has 0 submissions, we
        # catch this since it potentially stops celery
        gen_export = generate_export(
            Export.SAV_ZIP_EXPORT, 'zip', username, id_string, export_id,
            query, group_delimiter, split_select_multiples,
            binary_select_multiples
        )
    except (Exception, NoRecordsFoundError) as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("SAV ZIP Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e, sys.exc_info())
        raise
    else:
        return gen_export.id


@task()
def create_external_export(username, id_string, export_id, query=None,
                           token=None, meta=None):
    export = Export.objects.get(id=export_id)
    try:
        # though export is not available when for has 0 submissions, we
        # catch this since it potentially stops celery
        gen_export = generate_external_export(
            Export.EXTERNAL_EXPORT, username,
            id_string, export_id, token, query, meta
        )
    except (Exception, NoRecordsFoundError, ConnectionError) as e:
        export.internal_status = Export.FAILED
        export.save()
        # mail admins
        details = {
            'export_id': export_id,
            'username': username,
            'id_string': id_string
        }
        report_exception("External Export Exception: Export ID - "
                         "%(export_id)s, /%(username)s/%(id_string)s"
                         % details, e, sys.exc_info())
        raise
    else:
        return gen_export.id


@task()
def delete_export(export_id):
    try:
        export = Export.objects.get(id=export_id)
    except Export.DoesNotExist:
        pass
    else:
        export.delete()
        return True
    return False


SYNC_MONGO_MANUAL_INSTRUCTIONS = """
To re-sync manually, ssh into the server and run:

python manage.py sync_mongo -r [username] [id_string]\
--settings='settings.local_settings'

To force complete delete and re-creation, use the -a option:

python manage.py sync_mongo -ra [username] [id_string]\
--settings='settings.local_settings'
"""

REMONGO_PATTERN = re.compile(r'Total # of records to remongo: -?[1-9]+',
                             re.IGNORECASE)


@task()
def email_mongo_sync_status():
    """Check the status of records in the mysql db versus mongodb, and, if
    necessary, invoke the command to re-sync the two databases, sending an
    email report to the admins of before and after, so that manual syncing (if
    necessary) can be done."""

    before_report = mongo_sync_status()
    if REMONGO_PATTERN.search(before_report):
        # synchronization is necessary
        after_report = mongo_sync_status(remongo=True)
    else:
        # no synchronization is needed
        after_report = "No synchronization needed"

    # send the before and after reports, along with instructions for
    # syncing manually, as an email to the administrators
    mail_admins("Mongo DB sync status",
                '\n\n'.join([before_report,
                             after_report,
                             SYNC_MONGO_MANUAL_INSTRUCTIONS]))