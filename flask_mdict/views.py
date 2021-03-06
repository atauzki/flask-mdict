import io
import re
import os.path
import datetime
import urllib.parse
from io import StringIO, BytesIO

from flask import render_template, send_file, url_for,  \
    redirect, abort, jsonify, request, make_response

from .forms import WordForm
from . import mdict, get_mdict, get_db, Config
from . import helper


regex_word_link = re.compile(r'^(@@@LINK=)(.+)$')
# img src
regex_src_schema = re.compile(r'([ "]src=["\'])(/|file:///)?(?!data:)(.+?["\'])')
# http://.../
regex_href_end_slash = re.compile(r'([ "]href=["\'].+?)(/)(["\'])')
# sound://
regex_href_schema_sound = re.compile(r'([ "]href=["\'])(sound://)([^#].+?["\'])')
# entry://
regex_href_schema_entry = re.compile(r'([ "]href=["\'])(entry://)([^#].+?["\'])')
# default: http
regex_href_no_schema = re.compile(r'([ "]href=["\'])(?!sound://|entry://)([^#].+?["\'])')

# css
regex_css = re.compile(r'(<link.*? )(href)(=".+?>)')
# js
regex_js = re.compile(r'(<script.*? )(src)(=".+?>)')


@mdict.route('/search/<part>')
def query_part(part):
    contents = set()
    for uuid, item in get_mdict().items():
        if item['type'] == 'app':
            continue
        content = item['query'].get_mdx_keys(get_db(uuid), part)
        contents |= set(content)
    return jsonify(suggestion=sorted(contents))


@mdict.route('/<uuid>/resource/<path:resource>', methods=['GET', 'POST'])
def query_resource(uuid, resource):
    """query mdict resource file: mdd"""
    resource = resource.strip()
    item = get_mdict().get(uuid)
    if not item:
        abort(404)

    # file, load from cache, local, static, mdd
    fname = os.path.join(item['root_path'], resource)
    # check cache
    if resource in item:
        data = item['cache'][resource]
    elif os.path.exists(fname):
        # mdict local disk
        data = open(fname, 'rb').read()
    else:
        # mdict mdd
        q = item['query']
        if item['type'] == 'app':
            with mdict.open_resource(os.path.join('static', resource)) as f:
                data = f.read()
        else:
            key = '\\%s' % '\\'.join(resource.split('/'))
            data = q.mdd_lookup(get_db(uuid), key, ignorecase=True)
    if not data:
        # load from flask static
        if resource in ['logo.ico', 'css/reset.css', 'css/mdict.css']:
            with mdict.open_resource(os.path.join('static', resource)) as f:
                data = f.read()

    if data:
        ext = resource.rpartition('.')[-1]
        if resource not in item and ext in ['css', 'js', 'png', 'jpg', 'woff2']:
            if resource.endswith('.css'):
                try:
                    s_data = data.decode('utf-8')
                    s_data = helper.fix_css('#class_%s' % uuid, s_data)
                    data = s_data.encode('utf-8')
                    item['error'] = ''
                except Exception as err:
                    err_msg = 'Error: %s - %s' % (resource, err.format_original_error())
                    print(err_msg)
                    item['error'] = err_msg
                    abort(404)
            if Config.MDICT_CACHE:
                item['cache'][resource] = data        # cache css file

        bio = io.BytesIO()
        bio.write(data)
        bio.seek(0)

        resp = make_response(send_file(bio, attachment_filename=resource))
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    else:
        abort(404)


@mdict.route('/<uuid>/query/<word>', methods=['GET', 'POST'])
def query_word(uuid, word):
    """query mdict dict file: mdx"""
    form = WordForm()
    if form.validate_on_submit():
        word = form.word.data
    else:
        form.word.data = word

    word = word.strip()
    if uuid == 'default':
        uuid = list(get_mdict().keys())[0]
        return redirect(url_for('.query_word', uuid=uuid, word=word))

    item = get_mdict().get(uuid)
    if not item:
        abort(404)

    # entry and word, load from mdx, db
    q = item['query']
    if item['type'] == 'app':
        records = q(word, item)
    else:
        records = q.mdx_lookup(get_db(uuid), word, ignorecase=True)
    html_content = []
    if item['error']:
        html_content.append('<div style="color: red;">%s</div>' % item['error'])
    prefix_resource = '%s/resource' % '..'
    # prefix_entry = '%s/query' % '..'
    found_word = (uuid != 'gtranslate' and len(records) > 0)
    count = 1
    record_num = len(records)
    for record in records:
        if record.startswith('@@@LINK='):
            record_num -= 1
    for record in records:
        record = helper.fix_html(record)
        mo = regex_word_link.match(record)
        if mo:
            link = mo.group(2).strip()
            if '#' in link:
                # anchor in current page
                link, anchor = link.split('#')
                return redirect(url_for('.query_word', uuid=uuid, word=link, _anchor=anchor))
            else:
                if len(records) > 1:
                    record = f'<p>See also: <a href="entry://{link}">{link}</a></p>'
                else:
                    return redirect(url_for('.query_word', uuid=uuid, word=link))
        else:
            # for <img src="<add:resource/>..."
            record = regex_src_schema.sub(r'\g<1>%s/\3' % prefix_resource, record)
            # for <a href="sound://<add:resouce>..."
            record = regex_href_schema_sound.sub(r'\1\g<2>%s/\3' % prefix_resource, record)
            # for <a href="<add:resource/>image.png"
            record = regex_href_no_schema.sub(r'\g<1>%s/\2' % prefix_resource, record)
            # remove /
            record = regex_href_end_slash.sub(r'\1\3', record)
            # for <a href="entry://...", alread in query word page, do not add
            record = regex_href_schema_entry.sub(r'\1\2\3', record)
            # record = regex_href_schema_entry.sub(r'\1\g<2>%s/\3' % prefix_entry, record)

            # keep first css
            if count > 1:
                record = regex_css.sub(r'\1data-\2\3', record)
            # keep last js
            if count < record_num:
                record = regex_js.sub(r'\1data-\2\3', record)
            count += 1

        html_content.append(record)
    html_content = '<link rel="stylesheet" href="../resource/css/reset.css">' + '<hr />'.join(html_content)
    about = item['about']
    # fix about html. same above
    about = regex_href_end_slash.sub(r'\1\3', about)
    about = regex_src_schema.sub(r'\g<1>%s/\3' % prefix_resource, about)
    about = regex_href_schema_sound.sub(r'\1\g<2>%s/\3' % prefix_resource, about)

    contents = {}
    contents[uuid] = {
        'title': item['title'],
        'logo': item['logo'],
        'about': about,
        'content': html_content,
    }
    word_meta = helper.query_word_meta(word)
    history = helper.get_history()
    if found_word:
        helper.add_history(word)
    return render_template(
        'mdict/query.html',
        form=form,
        word=word,
        word_meta=word_meta,
        history=history,
        contents=contents,
    )


@mdict.route('/', methods=['GET', 'POST'])
def query_word_all():
    form = WordForm()
    if form.validate_on_submit():
        word = form.word.data
    else:
        word = request.args.get('word')
        word = word or helper.ecdict_random_word('cet4')
        form.word.data = word

    word = word.strip()
    contents = {}
    found_word = False
    for uuid, item in get_mdict().items():
        q = item['query']
        if item['type'] == 'app':
            records = q(word, item)
        else:
            records = q.mdx_lookup(get_db(uuid), word, ignorecase=True)
        html_content = []
        if item['error']:
            html_content.append('<div style="color: red;">%s</div>' % item['error'])
        prefix_resource = '%s/resource' % uuid
        prefix_entry = '%s/query' % uuid
        found_word = found_word or (uuid != 'gtranslate' and len(records) > 0)
        count = 1
        record_num = len(records)
        for record in records:
            if record.startswith('@@@LINK='):
                record_num -= 1
        for record in records:
            record = helper.fix_html(record)
            mo = regex_word_link.match(record)
            if mo:
                link = mo.group(2).strip()
                if '#' in link:
                    link, anchor = link.split('#')
                    record = f'<p>See also: <a href="entry://{link}#{anchor}">{link}</a></p>'
                else:
                    record = f'<p>See also: <a href="entry://{link}">{link}</a></p>'
            else:
                record = regex_href_end_slash.sub(r'\1\3', record)
                # add dict uuid into url
                # for resource
                record = regex_src_schema.sub(r'\g<1>%s/\3' % prefix_resource, record)
                record = regex_href_schema_sound.sub(r'\1\g<2>%s/\3' % prefix_resource, record)
                record = regex_href_no_schema.sub(r'\g<1>%s/\2' % prefix_resource, record)
                # for dict data
                record = regex_href_schema_entry.sub(r'\1\g<2>%s/\3' % prefix_entry, record)

                # keep first css
                if count > 1:
                    record = regex_css.sub(r'\1data-\2\3', record)
                # keep last js
                if count < record_num:
                    record = regex_js.sub(r'\1data-\2\3', record)
                count += 1

            html_content.append(record)

        html_content = f'<link rel="stylesheet" href="{url_for(".query_resource", uuid=uuid, resource="css/reset.css")}">' + '<hr />'.join(html_content)
        about = item['about']
        about = regex_src_schema.sub(r'\g<1>%s/\3' % prefix_resource, about)
        about = regex_href_end_slash.sub(r'\1\3', about)
        about = regex_href_schema_sound.sub(r'\1\g<2>%s/\3' % prefix_resource, about)
        contents[uuid] = {
            'title': item['title'],
            'logo': item['logo'],
            'about': about,
            'content': html_content,
        }

    word_meta = helper.query_word_meta(word)

    history = helper.get_history()
    if found_word:
        helper.add_history(word)
    return render_template(
        'mdict/query.html',
        form=form,
        word=word,
        word_meta=word_meta,
        history=history,
        contents=contents,
    )


@mdict.route('/gtranslate/query/<word>', methods=['GET', 'POST'])
def google_translate(word):
    trans = helper.google_translate(word)
    return '\n'.join(trans)


@mdict.route('/meta/<word>')
def query_word_meta(word):
    word_meta = helper.query_word_meta(word)
    html = []
    html.append('<span><small>%s</small></span>' % word_meta)
    return '\n'.join(html)


@mdict.route('/<uuid>/lite/')
def query_word_lite(uuid):
    def url_replace(mo):
        abs_url = mo.group(2)
        abs_url = re.sub(r'(?<!:)//', '/', abs_url)
        return ' data-abs-url' + mo.group(1) + abs_url + mo.group(3)

    all_result = request.args.get('all_result', '') == 'true'
    fallback = request.args.get('fallback', '').split(',')
    word = request.args.get('word').strip()
    if uuid == 'default':
        items = [list(get_mdict().values())[0]]
        for f in fallback:
            if f in get_mdict():
                items.append(get_mdict().get(f))
    elif uuid == 'all':
        items = list(get_mdict().values())
    else:
        item = get_mdict().get(uuid)
        if not item:
            abort(404)
        items = [item]
        for f in fallback:
            if f in get_mdict():
                items.append(get_mdict().get(f))

    html_contents = []
    found_word = False
    for item in items:
        # entry and word, load from mdx, db
        cur_uuid = item['uuid']
        q = item['query']
        if item['type'] == 'app':
            records = q(word, item)
        else:
            records = q.mdx_lookup(get_db(cur_uuid), word, ignorecase=True)
        if not records:
            continue
        html = []
        html.append(f'<div id="class_{cur_uuid}">')
        html.append('<div class="mdict">')
        # add mdict_uuid by query_resource
        html.append(f'''<link rel="stylesheet"
                    href="{url_for(".query_resource", uuid=uuid, resource="css/reset.css", _external=True)}">''')
        html.append(f'''<link rel="stylesheet"
                    href="{url_for(".query_resource", uuid=uuid, resource="css/mdict.css", _external=True)}">''')
        if item['error']:
            html.append('<div style="color: red;">%s</div>' % item['error'])
        html.append('<div class="mdict-title">')
        html.append(f'''<img
                    style="height:16px !important;
                    border-radius:.25rem !important;
                    vertical-align:baseline !important"
                    src="{url_for(".query_resource", uuid=cur_uuid, resource=item["logo"], _external=True)}"/>''')
        html.append(item['title'])
        html.append('</div>')
        prefix_resource = f'{url_for(".query_resource", uuid=cur_uuid, resource="", _external=True)}'
        prefix_entry = f'{url_for(".query_word_lite", uuid=cur_uuid, word="", _external=True)}'
        found_word = found_word or (cur_uuid != 'gtranslate' and len(records) > 0)
        count = 1
        record_num = len(records)
        for record in records:
            if record.startswith('@@@LINK='):
                record_num -= 1
        for record in records:
            record = helper.fix_html(record)
            mo = regex_word_link.match(record)
            if mo:
                link = mo.group(2).strip()
                if '#' in link:
                    # anchor in current page
                    link, anchor = link.split('#')
                    return redirect(url_for('.query_word_lite', uuid=cur_uuid, word=link, _anchor=anchor))
                else:
                    if len(records) > 1:
                        record = f'''<p>See also: <a href="entry://{url_for(".query_word_lite", uuid=cur_uuid, word=link)}">{link}</a></p>'''
                    else:
                        return redirect(url_for('.query_word_lite', uuid=cur_uuid, word=link))
            else:
                # remove http:// from sound:// and entry://
                record = regex_href_end_slash.sub(r'\1\3', record)
                # <img src="<add:resource/>...
                record = regex_src_schema.sub(r'\g<1>%s/\3' % prefix_resource, record)
                # <a href="sound://<add:resource/>...
                record = regex_href_schema_sound.sub(r'\1\g<2>%s/\3' % prefix_resource[7:], record)
                # <a href="<add:resource/>image.png
                record = regex_href_no_schema.sub(r'\g<1>%s/\2' % prefix_resource, record)
                # entry://
                record = regex_href_schema_entry.sub(r'\1\g<2>%s\3' % prefix_entry[7:], record)

                # keep first css
                if count > 1:
                    record = regex_css.sub(r'\1data-\2\3', record)
                # keep last js
                if count < record_num:
                    record = regex_js.sub(r'\1data-\2\3', record)
                count += 1

            html.append(record)
        html.append('</div></div>')
        # no template, add mdict.js link
        html.append(f'<script src="{url_for(".static", filename="js/mdict.js", _external=True)}"></script>')
        html = '\n'.join(html)
        # fix url with "//"
        # css, image
        html = re.sub(r'( href=")(.+?)(")', url_replace, html)
        # script
        html = re.sub(r'( src=")(?!data:)(.+?)(")', url_replace, html)
        html_contents.append(html)
        if uuid != 'all' and not all_result:
            break
    resp = make_response('<hr class="seprator" />'.join(html_contents))
    resp.headers['Access-Control-Allow-Origin'] = '*'
    if found_word:
        helper.add_history(word)
    return resp


@mdict.route('/list/')
def list_mdict():
    def src_replace(mo):
        link = mo.group(2)
        url = url_for('.query_resource', uuid=v['uuid'], resource=link, _external=True)
        return mo.group(1) + url + mo.group(3)

    uuid = request.args.get('uuid')
    regex_img = re.compile(r'( src=")(.+?)(")')

    all_mdict = []
    for k, v in get_mdict().items():
        if uuid and uuid != k:
            continue
        all_mdict.append({
            'title': v['title'],
            'uuid': v['uuid'],
            'logo': url_for('.query_resource', uuid=v['uuid'], resource=v['logo'], _external=True),
            'about': regex_img.sub(src_replace, v['about']),
            'type': v['type'],
            'lite_url': url_for('.query_word_lite', uuid=v['uuid'], word='', _external=True),
            'url': url_for('.query_word', uuid=v['uuid'], word='', _external=True),
        })

    return jsonify(all_mdict)


@mdict.route('/clear_history/')
def clear_history():
    helper.clear_history()
    return redirect(url_for('.query_word_all'))


@mdict.route('/export_history/')
def export_history():
    now = datetime.datetime.now()
    filename = f'history-{now.strftime("%Y%m%d")}.csv'
    sio = StringIO()
    helper.export_history(sio)
    bio = BytesIO(sio.getvalue().encode('utf-8'))
    bio.seek(0)
    sio.close()

    return send_file(
        bio,
        mimetype='text/csv',
        as_attachment=True,
        attachment_filename=filename,
        last_modified=now,
    )
