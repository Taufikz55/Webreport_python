import pathlib
import datetime
import sqlite3
from contextlib import closing
try:
    from http.client import responses
except ImportError:
    from httplib import responses

LOGROOT = '/var/log/apache2/'
DATABASE = '/tmp/loglines_{}.db'
responses.update({444: 'No Response From Server',
                  494: 'Request Header Too Large',
                  495: 'SSL Certificate Error',
                  496: 'SSL Certificate Required',
                  497: 'HTTP Request Sent to HTTPS Port',
                  499: 'Client Closed Request'})

def listlogs():
    lijst = []
    for item in (x for x in pathlib.Path(LOGROOT).iterdir() if x.suffix == '.log'):
        lijst.append((item.stat().st_ctime, item.name))
    lijst.sort()
    lijst.reverse()
    return [x[1] for x in lijst]

def connect_db(timestr):
    return sqlite3.connect(DATABASE.format(timestr))

def init_db(timestr):
    with closing(connect_db(timestr)) as db:
        cur = db.cursor()
        cur.execute('DROP TABLE IF EXISTS parms;')
        cur.execute('CREATE TABLE parms (id INTEGER PRIMARY KEY, '
                    'logfile STRING NOT NULL, entries INTEGER NOT NULL, '
                    'current INTEGER NOT NULL, total INTEGER NOT NULL, '
                    'ordering STRING NOT NULL, mld STRING NOT NULL);')
        db.commit()
        cur.execute('INSERT INTO parms VALUES (?, ?, ?, ?, ?, ?, ?)', (1, '', 10,
                                                                       0, 0, 'desc', ''))
        db.commit()


def startswith_date(line):
    if line.startswith('['):
        test = line[1:].split(':', 1)
        dateformat =  '%a %b %H %M:%S.%f %%Y'
    else:
        test = line.split(None, 1)
        dateformat = '%Y/%m/%d'
    if not len(test) == 2:
        return False
    try:
        date = datetime.datetime.strptime(test[0], dateformat)
    except ValueError:
        return False
    return True


def rereadlog(logfile, entries, order, timestr):
    old_logfile, old_entries, old_order = logfile, entries, order
    with closing(connect_db(timestr)) as db:
        cur = db.cursor()
        try:
            data = cur.execute('SELECT logfile, entries, ordering FROM parms '
                               'where id == 1')
        except sqlite3.OperationalError:
            init_db(timestr)
        else:
            for row in data:
                old_logfile, old_entries, old_order = row
                break
    if logfile == old_logfile and entries == old_entries and order == old_order:
        return
    with closing(connect_db(timestr)) as db:
        cur = db.cursor()
        cur.execute('UPDATE parms SET logfile = ?, entries = ? , ordering = ? '
                    'WHERE id == 1', (logfile, entries, order))
        db.commit()
    with (pathlib.Path(LOGROOT) / logfile).open() as _in:
        data = _in.readlines()
    if not data:
        try:
            with (pathlib.Path(LOGROOT) / (logfile + '.1')).open() as _in:
                data = _in.readlines()
        except FileNotFoundError:
            pass
    total = len(data)
    with closing(connect_db(timestr)) as db:
        cur = db.cursor()
        parms = cur.execute('SELECT ordering FROM parms where id == 1')
        for row in parms:
            order = row[0]
            break
        cur.execute('DROP TABLE IF EXISTS log;')
        cur.execute('CREATE TABLE log (id INTEGER PRIMARY KEY, '
                    'line varchar(1000) NOT NULL);')
        db.commit()
        if order == 'desc':
            data.reverse()
        for ix, line in enumerate(data):
            cur.execute('INSERT INTO log VALUES (?, ?)', (ix + 1, line))
        db.commit()
        check = cur.execute('SELECT COUNT(*) FROM log;')
        for item in check:
            check = item[0]
            break
        if check != total:
            raise ValueError('Why this difference between {} and {}?'.format(total,
                                                                            check))
        else:
            cur.execute('UPDATE parms SET total = ? WHERE id == 1', (total,))
            db.commit()

def get_data(timestr, position='first'):
    outdict = {'loglist': listlogs(),
               'logfile': '',
               'order': '',
               'errorlog': False,
               'numentries': ('10', '30', '50', '100',),
               'entries': '',
               'mld': '',
               'logdata': [],
               'timestr': timestr}
    with closing(connect_db(timestr)) as db:
        cur = db.cursor()
        try:
            data = cur.execute('SELECT logfile, entries, current, total, ordering, '
                               'mld FROM parms where id == 1')
        except sqlite3.OperationalError:
            init_db(timestr)
            outdict['mld'] = 'No data available, try refreshing the display'
        else:
            for row in data:
                logfile, entries, current, total, order, mld = row
                break
            is_errorlog = True if 'error' in logfile else False
            outdict['logfile'] = logfile
            outdict['order'] = order
            outdict['errorlog'] = is_errorlog
            outdict['entries'] = str(entries)
            outdict['mld'] = mld
            if position == 'first':
                current = 1
            elif position == 'prev':
                newtop = current - entries
                if newtop > 0:
                    current = newtop
                else:
                    outdict['mld'] = 'Tidak Ada Halaman Sebelumnya'
            elif position == 'next':
                newtop = current + entries
                if newtop <= total:
                    current = newtop
                else:
                    outdict['mld'] = 'Tidak Ada Halaman Berikutnya'
            elif position == 'last':
                current = int(total / entries) * entries + 1
            cur.execute('UPDATE parms SET current = ? WHERE id == 1', (current,))
            db.commit()

            if logfile:
                lines = cur.execute('SELECT line FROM log WHERE id BETWEEN {} '
                                    'and {}'.format(current, current + entries - 1))
                for line in lines:
                    if is_errorlog:
                        parts = showerror(line[0])
                    else:
                        parts = showaccess(line[0])
                    outdict['logdata'].append(parts)
            start = len(outdict['logdata'])
            for i in range(start, entries):
                outdict['logdata'].append({'client': '', 'date': '', 'data': ''})
    return outdict

def showerror(text):
    errortypes = ('[notice]', '[error]', '[crit]')
    client, date, data = '', '', ''
    for item in errortypes:
        if item in text:
            date, data = text.split(item)
            data = item + data
            break
    if not date:
        test = text.split(']', 1)
        if len(test) == 2 and test[0].startswith('['):
            date, text = test
            date = date[1:]
        data = text
    if '[client' in data:
        start, rest = data.split('[client', 1)
        if '[client' in rest:
            client_part_1, rest = rest.split('[client', 1)
            client_part_2, rest = rest.split(None, 1)
            if client_part_2[:-1] in client_part_1:
                client_part_2 = ''
        else:
            client_part_1, rest = rest.split(None, 1)
            client_part_2 = ''
        client = '[client:' + client_part_1
        if client_part_2:
            client += '/' + client_part_2
        data = start + rest
    parts = {"client": client, "date": date, "data": data}
    return parts
   # if ' [client' in data:
     #  start, rest = data.split('[client', 1)
     #  client_part_1, rest = rest.split('[client', 1)
     #  client_part_2, rest = rest.split(None, 1)
     #  if client_part_2[:-1] in client_part_1:
     #       client_part_2 = ''
     #  client = '[client :' + client_part_1
     #  if client_part_2:
     #       client += ' / ' + client_part_2

def showaccess(text):
    parts = {'client': '', 'date': '', 'data': ''}
    parsed = text.split(' -', 2)
    parts['client'] = parsed[0]
    if len(parsed) < 2:
        return parts
    parsed = parsed[-1].split(' [', 1)
    if len(parsed) < 2:
        return parts
    parsed = parsed[1].split('] "', 1)
    parts['date'] = parsed[0]
    if len(parsed) < 2:
        return parts
    parsed = parsed[1].split('" ', 1)
    if len(parsed) < 2:
        return parts
    command = parsed[0]
    parsed = parsed[1].split(' ', 1)
    try:
        text = responses[int(parsed[0])]
    except KeyError:
        text = 'unknown status'
    parts['data'] = '{} {}: {}'.format(parsed[0], text, command)
    return parts
