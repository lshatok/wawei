#!/usr/bin/env python
"""
hbase_backup script
    This script will run a map reduce Export job, send the parts to S3, and keep a version on
    a local mount.  7 days/copies will be kept on S3 (configurable).

Variables which need to be updated:
    AWS_ACCESS = ( AWS_KEY_ID, AWS_KEY_SECRET )
    s3bucket = 'net.webtelemetry().ssn.oge.backup'
    backup_path_local = '/vol2/backup'
    backup_path_hdfs = '/tmp/backup'
    distcp_log_path = '/tmp/backup/logs'
    Hadoop_NameServer = servername:port
    S3Retain = 7
    Hbase_Tables = ['meter_data','snmp_data']
    Hbase_Scanner_cache = '30000'
    Mapred_Compression = 'org.apache.hadoop.io.compress.GzipCodec'

Known issues:
    distcp has a bug when the AWS_KEY contains a special character on the CLI

"""
USAGE = "hbase_backup.py ( --start | --copytoS3 | --copytolocal <date_folder> )"

################################################
__author__ = 'Leo Shatokhin'
__copyright__ = "Copyright 2017, WebTelemetry US Inc."
__version__ = "0.7.6"
__maintainer__ = "Larry Sellars"
__email__ = "larry.sellars@webtelemetry.us"
__status__ = "Production"
################################################

## -------- Update for Environment -----------##
AWS_ACCESS = ('', '')
s3bucket = 'net.webtelemetry().ssn.oge.backup'

backup_path_local = '/vol02/backup'
backup_path_hdfs = '/tmp/backup'
distcp_log_path = '/tmp/backup/logs'
S3Retain = 7
Hbase_Tables = ['meter_data_oge_prod', 'snmp_data_oge_prod']
Hadoop_NameServer = '10.18.0.181:8020'
Hbase_Scanner_cache = '100000'
################################################

import os
import re
import shutil
import subprocess
import sys
from boto.s3.connection import S3Connection
from boto.s3.key import Key
from datetime import datetime

os.environ['JAVA_HOME'] = '/usr/lib/jvm/j2sdk1.6-oracle'

Today = datetime.now()
DateStr = Today.strftime('%Y-%m-%d')


def hdfs_cleanup():
    """
    Clear out the /tmp/backup hdfs path
    Return: exit code
    """
    ## hardcoding this directory for now ##
    h_cmd = ['hadoop', 'fs', '-rmr', '-skipTrash', '/tmp/backup/*']
    print "CMD: ", ' '.join(h_cmd)
    r_code = subprocess.call(h_cmd)
    return r_code


def local_cleanup():
    """
    Rotate the local copy if needed
    """
    PrevPath = '%s/prev' % (backup_path_local)
    if os.path.exists('%s/curr' % backup_path_local):
        if os.path.exists(PrevPath):
            shutil.rmtree(PrevPath)
        os.rename('%s/curr' % backup_path_local, PrevPath)


def hbase_export(hbase_tbl):
    """
    Execute the hbase export command
    Return: exit code
    """
    hdfs_backup_path = os.path.join(backup_path_hdfs, DateStr, hbase_tbl)
    h_cmd = ['hbase', 'org.apache.hadoop.hbase.mapreduce.Export',
             '-Dhbase.client.scanner.caching=%s' % (Hbase_Scanner_cache), '-Dmapred.output.compress=true',
             '-Dmapred.output.compression.codec=org.apache.hadoop.io.compress.GzipCodec', hbase_tbl, hdfs_backup_path]
    print "CMD: ", ' '.join(h_cmd)
    r_code = subprocess.call(h_cmd)
    return r_code


def xfer_hdfs_to_local(Hdfs_path, Local_path):
    """
    Make a HDFS copy to local
    Return: exit_code
    """
    if not os.path.exists(Local_path):
        os.makedirs(Local_path)
    h_cmd = ['hadoop', 'fs', '-copyToLocal', Hdfs_path, Local_path]
    print "CMD: ", ' '.join(h_cmd)
    r_code = subprocess.call(h_cmd)
    return r_code


def s3_cleanup(Bucket):
    """
    Rotate the backup copies in the S3 bucket
    """
    ## we only process keys that match the format YYYY-MM-DD
    d_format = re.compile('\d{4}-\d{2}-\d{2}')
    conn = S3Connection(AWS_ACCESS[0], AWS_ACCESS[1])
    bucket = conn.get_bucket(Bucket)
    S3files = []
    for k in bucket.list():
        fname = k.name.encode('utf-8')
        if fname.startswith('backups/') and re.search('\d{4}-\d{2}-\d{2}', fname):
            S3files.append(fname)
    ## Get list of Dated indexes in Bucket ##
    # Bucket_Dates = []
    # for key in S3files:
    #    isDate = d_format.match(key)
    #    if isDate:
    #        d = key[:10]
    #        if d not in Bucket_Dates:
    #            Bucket_Dates.append(d)
    ## Keep only S3Retain days ##
    Bucket_Dates.sort(reverse=True)
    while len(Bucket_Dates) > S3Retain:
        rmIndex = Bucket_Dates.pop()
        for k in bucket.list(prefix=rmIndex):
            k.delete()


def boto_cp_to_s3(Local_path, Bucket):
    """
    Copy to files from :Local_path to :Hdfs_path :Bucket
    """
    conn = S3Connection(AWS_ACCESS[0], AWS_ACCESS[1])
    bucket = conn.get_bucket(Bucket)
    ## Create list of files from Local_path ##
    KeyList = []
    for root, dirnames, filenames in os.walk(Local_path):
        for file in filenames:
            full_path = os.path.join(root, file)
            lstr = os.path.normpath(Local_path) + os.sep
            s3_path = full_path.replace(lstr, '', 1)
            KeyList.append((full_path, s3_path))
    s3k = Key(bucket)
    for k in KeyList:
        print "Copying file: %s" % (k[1])
        s3k.key = k[1]
        s3k.set_contents_from_filename(k[0])


def distcp_to_s3(Log, NameSvr, Hdfs_path, Bucket):
    """
    Use Distcp to transfer the hdfs backup to S3
    Return: exit_code
    """
    h_cmd = ['hadoop', 'distcp', '-log', Log, 'hdfs://%s/%s' % (NameSvr, Hdfs_path),
             's3n://%s:%s@%s/' % (AWS_ACCESS[0], AWS_ACCESS[1], Bucket)]
    print "CMD: ", h_cmd
    r_code = subprocess.call(h_cmd)
    return r_code


if __name__ == "__main__":
    hdfs_from_path = '%s/%s/' % (backup_path_hdfs, DateStr)
    Logpath = '%s/%s' % (distcp_log_path, DateStr)
    local_curr = '%s/curr' % (backup_path_local)

    try:
        if sys.argv[1] in ['help', '-h', '-?', '--help']:
            print USAGE
            sys.exit(0)
        if sys.argv[1] == '--copytoS3':
            print "Copying to S3 (boto)."
            boto_cp_to_s3(local_curr, s3bucket)
            print "[done]"
            sys.exit()
        if sys.argv[1] == '--copytolocal':
            try:
                date_folder = sys.argv[2]
                hdfs_from_path = os.path.join(backup_path_hdfs, date_folder)
            except IndexError:
                print "Missing arg: date_folder"
                print USAGE
                sys.exit(1)
            print "Copying to Local: %s" % (hdfs_from_path)
            print " .. cleaning local"
            local_cleanup()
            print " .. hdfs copy started"
            C = xfer_hdfs_to_local(hdfs_from_path, local_curr)
            print "[done]"
            sys.exit(C)
        if sys.argv[1] == '--start':
            pass
    except IndexError:
        print USAGE
        sys.exit(1)

    ## Cleanup FS ##
    hdfs_cleanup()
    ## Process hbase export for each table ##
    for Hbase_Table in Hbase_Tables:
        R = hbase_export(Hbase_Table)
        if R > 0:
            print "[Error] hbase_export(%s) ret_code: %s" % (Hbase_Table, R)
            sys.exit(1)

    print "Copying to Local."
    local_cleanup()
    xfer_hdfs_to_local(hdfs_from_path, local_curr)

    print "Copying to S3 (boto)"
    boto_cp_to_s3(local_curr, s3bucket)
    print "[done]"

    ## Cleanup ##
    print "Cleaning up S3 bucket."
    s3_cleanup(s3bucket)

    print "Done."
