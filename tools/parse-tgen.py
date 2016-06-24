#!/usr/bin/python

import sys, os, argparse, re, json
from multiprocessing import Pool, cpu_count
from subprocess import Popen, PIPE
from signal import signal, SIGINT, SIG_IGN
#from Canvas import Line
import pdb
from lxml import etree

DESCRIPTION="""
A utility to help parse results from the tgen traffic generator.

This script enables processing of tgen log files and storing processed
data in json format for plotting. It was written so that the log files
need never be stored on disk decompressed, which is useful when log file
sizes reach tens of gigabytes.

Use the help menu to understand usage:
$ python parse-tgen.py -h

The standard way to run the script is to give the path to a directory tree
under which one or several tgen log files exist:
$ python parse-tgen.py shadow.data/hosts/
$ python parse-tgen.py ./

This path will be searched for log files whose names match those created
by shadow; additional patterns can be added with the '-e' option.

A single tgen log file can also be passed on STDIN with the special '-' path:
$ cat tgen.log | python parse-tgen.py -
$ xzcat tgen.log.xz | python parse-tgen.py -

The default mode is to filter and parse the log files using a single
process; this will be done with multiple worker processes when passing
the '-m' option.
"""

TGENJSON="stats.tgen.json"

def main():
    parser = argparse.ArgumentParser(
        description=DESCRIPTION, 
        formatter_class=argparse.RawTextHelpFormatter)#ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        help="""The PATH to search for tgen log files, which may be '-'
for STDIN; each log file may end in '.xz' to enable
inline xz decompression""", 
        metavar="PATH",
        action="store", dest="searchpath")

    parser.add_argument('-e', '--expression',
        help="""Append a regex PATTERN to the list of strings used with
re.search to find tgen log file names in the search path""", 
        action="append", dest="patterns",
        metavar="PATTERN",
        default=["tgen.*\.log"])

    parser.add_argument('-m', '--multiproc',
        help="""Enable multiprocessing with N worker process, use '0'
to use the number of processor cores""",
        metavar="N",
        action="store", dest="nprocesses", type=type_nonnegative_integer,
        default=1)

    parser.add_argument('-p', '--prefix', 
        help="""A STRING directory path prefix where the processed data
files generated by this script will be written""", 
        metavar="STRING",
        action="store", dest="prefix",
        default=os.getcwd())
    parser.add_argument('-o', '--filter',
        help="""A STRING list of comma separated country identifier name for country to filter out -
        output two files, one with data from the countries specified and one
        for all remaining clients.
        if it starts by 'All but ...', apply reverse logic.
        PATH is the path towards the config.xml file""",
        metavar=("STRING", "PATH"),
        nargs=2,
        action="store", dest="filter",
        default=None)
    args = parser.parse_args()
    args.searchpath = os.path.abspath(os.path.expanduser(args.searchpath))
    args.prefix = os.path.abspath(os.path.expanduser(args.prefix))
    if args.nprocesses == 0: args.nprocesses = cpu_count()
    run(args)

def run(args):
    
    listlogfilepaths = find_file_paths(args.searchpath, args.patterns, args.filter)
    print >> sys.stderr, "processing input from {0} files...".format(len(logfilepaths))
    i=0
    for logfilepaths in listlogfilepaths:
        p = Pool(args.nprocesses)
        r = []
        try:
            mr = p.map_async(process_tgen_log, logfilepaths)
            p.close()
            while not mr.ready(): mr.wait(1)
            r = mr.get()
        except KeyboardInterrupt:
            print >> sys.stderr, "interrupted, terminating process pool"
            p.terminate()
            p.join()
            sys.exit()

        d = {'nodes':{}}
        name_count, noname_count, success_count, error_count = 0, 0, 0, 0
        for item in r:
            if item is None:
                continue
            name, data = item[0], item[1]
            if name is None:
                noname_count += 1
                continue
            name_count += 1
            d['nodes'][name] = data
            success_count += item[2]
            error_count += item[3]

        print >> sys.stderr, "done processing input: {0} total successes, {1} total errors, {2} files with names, {3} files without names".format(success_count, error_count, name_count, noname_count)
        print >> sys.stderr, "dumping stats in {0}".format(args.prefix)
        if args.filter is not None and i == 0:
            dump(d, args.prefix.join('%s'.format(args.filter[0])), TGENJSON)
        else:
            dump(d, args.prefix, TGENJSON)

        i+=1
    print >> sys.stderr, "all done!"

def process_tgen_log(filename):
    signal(SIGINT, SIG_IGN) # ignore interrupts
    source, xzproc = source_prepare(filename)

    d = {'firstbyte':{}, 'lastbyte':{}, 'errors':{}}
    name = None
    success_count, error_count = 0, 0

    for line in source:
        try:
            if name is None and re.search("Initializing traffic generator on host", line) is not None:
                name = line.strip().split()[11]
            elif re.search("transfer-complete", line) is not None or re.search("transfer-error", line) is not None:
                parts = line.strip().split()
                if len(parts) < 26: continue
                sim_seconds = timestamp_to_seconds(parts[2])
                second = int(sim_seconds)

                ioparts = parts[13].split('=')
                iodirection = ioparts[0]
                if 'read' not in iodirection: continue #return None # this is a server, do we want its stats?
                bytes = int(ioparts[1].split('/')[0])

                if 'transfer-complete' in parts[6]:
                    success_count += 1
                    cmdtime = int(parts[21].split('=')[1])/1000000.0
                    rsptime = int(parts[22].split('=')[1])/1000000.0
                    fbtime = int(parts[23].split('=')[1])/1000000.0
                    lbtime = int(parts[24].split('=')[1])/1000000.0
                    chktime = int(parts[25].split('=')[1])/1000000.0

                    if bytes not in d['firstbyte']: d['firstbyte'][bytes] = {}
                    if second not in d['firstbyte'][bytes]: d['firstbyte'][bytes][second] = []
                    d['firstbyte'][bytes][second].append(fbtime-cmdtime)

                    if bytes not in d['lastbyte']: d['lastbyte'][bytes] = {}
                    if second not in d['lastbyte'][bytes]: d['lastbyte'][bytes][second] = []
                    d['lastbyte'][bytes][second].append(lbtime-cmdtime)

                elif 'transfer-error' in parts[6]:
                    error_count += 1
                    code = parts[10].strip('()').split(',')[8].split('=')[1]
                    if code not in d['errors']: d['errors'][code] = {}
                    if second not in d['errors'][code]: d['errors'][code][second] = []
                    d['errors'][code][second].append(bytes)
        except: continue # data format error

    source_cleanup(filename, source, xzproc)
    return [name, d, success_count, error_count]

def find_file_paths(searchpath, patterns, filter_country):
    paths = []
    clients = []
    country_path = []
    remainder_path = []
    allBut = 'All but' in filter_country
    if filter_country is not None:
        tree = etree.parse(filter_country[1])
        root = tree.getroot()
        if allBut: 
            filter_country = filter_country[8:].split(',')
        else:
            filter_country = filter_country.split(',')
        for country in filter_country:
            pdb.set_trace()
            clients.append(root.findall("./node[@typehint='client', @geocodehint='%s']"
                .format(country)))
    if searchpath.endswith("/-"): paths.append("-")
    else:
        pdb.set_trace()
        for root, dirs, files in os.walk(searchpath):
            for name in files:
                found = False
                fpath = os.path.join(root, name)
                fbase = os.path.basename(fpath)
                for pattern in patterns:
                    if re.search(pattern, fbase): found = True
                if found: paths.append(fpath)
    return paths

def type_nonnegative_integer(value):
    i = int(value)
    if i < 0: raise argparse.ArgumentTypeError("%s is an invalid non-negative int value" % value)
    return i

def timestamp_to_seconds(stamp): # unix timestamp
    return float(stamp)

def source_prepare(filename):
    source, xzproc = None, None
    if filename == '-':
        source = sys.stdin
    elif filename.endswith(".xz"):
        xzproc = Popen(["xz", "--decompress", "--stdout", filename], stdout=PIPE)
        source = xzproc.stdout
    else:
        source = open(filename, 'r')
    return source, xzproc

def source_cleanup(filename, source, xzproc):
    if xzproc is not None: xzproc.wait()
    elif filename != '-': source.close()

def dump(data, prefix, filename, compress=True):
    if not os.path.exists(prefix): os.makedirs(prefix)
    if compress: # inline compression
        with open("/dev/null", 'a') as nullf:
            path = "{0}/{1}.xz".format(prefix, filename)
            xzp = Popen(["xz", "--threads=3", "-"], stdin=PIPE, stdout=PIPE)
            ddp = Popen(["dd", "of={0}".format(path)], stdin=xzp.stdout, stdout=nullf, stderr=nullf)
            json.dump(data, xzp.stdin, sort_keys=True, separators=(',', ': '), indent=2)
            xzp.stdin.close()
            xzp.wait()
            ddp.wait()
    else: # no compression
        path = "{0}/{1}".format(prefix, filename)
        with open(path, 'w') as outf: json.dump(data, outf, sort_keys=True, separators=(',', ': '), indent=2)

if __name__ == '__main__': sys.exit(main())

