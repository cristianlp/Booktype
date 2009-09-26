"""Various things to do with [x]html that might be useful in more than
one place."""

import lxml, lxml.html, lxml.etree, lxml.html.clean

import os, sys
import re
from cStringIO import StringIO

from urlparse import urlparse, urlsplit, urljoin
from urllib2 import urlopen, HTTPError

try:
    import simplejson as json
except ImportError:
    import json

from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED, ZIP_STORED


MEDIATYPES = {
    'html': "text/html",
    'xhtml': "application/xhtml+xml",
    'css': 'text/css',
    'json': "application/json",

    'png': 'image/png',
    'gif': 'image/gif',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'svg': 'image/svg+xml',

    'ncx': 'application/x-dtbncx+xml',
    'dtb': 'application/x-dtbook+xml',
    'xml': 'application/xml',

    'pdf': "application/pdf",
    'txt': 'text/plain',

    'epub': "application/epub+zip",
    'booki': "application/booki+zip",

    None: 'application/octet-stream',
}

OK_TAGS = set([
    "body", "head", "html", "title", "abbr", "acronym", "address",
    "blockquote", "br", "cite", "code", "dfn", "div", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "kbd", "p", "pre", "q", "samp", "span",
    "strong", "var", "a", "dl", "dt", "dd", "ol", "ul", "li", "object",
    "param", "b", "big", "hr", "i", "small", "sub", "sup", "tt", "del",
    "ins", "bdo", "caption", "col", "colgroup", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "img", "area", "map", "meta", "style",
    "link", "base"
    ])

XHTMLNS = '{http://www.w3.org/1999/xhtml}'
XHTML = 'http://www.w3.org/1999/xhtml'

CHAPTER_TEMPLATE = '''<html>
<head>
<title>%(title)s</title>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
</head>
<body>
%(text)s
</body>
</html>
'''

XHTML11_DOCTYPE = '''<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
    "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
'''
XML_DEC = '<?xml version="1.0" encoding="UTF-8"?>\n'

IMG_CACHE = 'cache/images/'
IMG_PREFIX = 'static/'

def log(*messages, **kwargs):
    for m in messages:
        try:
            print >> sys.stderr, m
        except Exception:
            print >> sys.stderr, repr(m)


class Author(object):
    def __init__(self, name, email):
        self.name = name
        self.email = email

def url_to_filename(url, prefix=''):
    #XXX slightly inefficient to do urlsplit so many times, but versitile
    fragments = urlsplit(url)
    base, ext = fragments.path.rsplit('.', 1)
    server = fragments.netloc.split('.', 1)[0] #en, fr, translate
    base = base.split('/pub/', 1)[1] #remove /floss/pub/ or /pub/
    base = re.sub(r'[^\w]+', '-',  '%s-%s' %(base, server))
    return '%s%s.%s' % (prefix, base, ext)


class ImageCache(object):
    def __init__(self, cache_dir=IMG_CACHE, prefix=IMG_PREFIX):
        self._fetched = {}
        self.cache_dir = cache_dir
        self.prefix = prefix
        if not os.path.exists(cache_dir + prefix):
            os.makedirs(cache_dir + prefix)

    def read_local_url(self, path):
        f = open(self.cache_dir + path)
        s = f.read()
        f.close()
        return s

    def _save_local_url(self, path, data):
        f = open(self.cache_dir + path, 'w')
        f.write(data)
        f.close()
        #os.chmod(path, 0444)

    def fetch_if_necessary(self, url, target=None, use_cache=True):
        if url in self._fetched:
            return self._fetched[url]

        if target is None:
            target = url_to_filename(url, self.prefix)

        if use_cache and os.path.exists(self.cache_dir + target):
            log("used cache for %s" % target)
            return target

        try:
            f = urlopen(url)
            data = f.read()
            f.close()
        except HTTPError, e:
            # if it is missing, assume it will be missing every time
            # after, otherwise, you can get into endless waiting
            self._fetched[url] = None
            log("Wanting '%s', got error %s" %(url, e))
            return None

        self._save_local_url(target, data)
        self._fetched[url] = target
        return target


class BaseChapter(object):
    image_cache = ImageCache()

    def load_tree(self, text=None, html=None):
        """Parse the chapter as html.  If the chapter is complete
        html, use the html argument; for TWiki pages use text."""
        if html is None:
            html = CHAPTER_TEMPLATE % {
                'title': '%s: %s' % (self.book, self.name),
                'text': text
                }
        self.tree = lxml.html.document_fromstring(html)

    def as_html(self):
        """Serialise the tree as html."""
        return lxml.etree.tostring(self.tree, method='html')

    def as_twikitext(self):
        """Get the twiki-style guts of the chapter from the tree"""
        text = lxml.etree.tostring(self.tree.find('body'), method='html')
        text = re.sub(r'^.*?<body.*?>\s*', '', text)
        text = re.sub(r'\s*</body>.*$', '\n', text)
        return text

    def as_xhtml(self):
        """Convert to xhtml and serialise."""
        try:
            root = self.tree.getroot()
        except AttributeError:
            root = self.tree

        nsmap = {None: XHTML}
        xroot = lxml.etree.Element(XHTMLNS + "html", nsmap=nsmap)

        def xhtml_copy(el, xel):
            xel.text = el.text
            for k, v in el.items():
                xel.set(k, v)
            for child in el.iterchildren():
                xchild = xel.makeelement(XHTMLNS + child.tag)
                xel.append(xchild)
                xhtml_copy(child, xchild)
            xel.tail = el.tail

        xhtml_copy(root, xroot)

        return XML_DEC + XHTML11_DOCTYPE + lxml.etree.tostring(xroot)

    def localise_links(self):
        """Find image links, convert them to local links, and fetch
        the images from the net so the local links work"""
        images = []
        def localise(oldlink):
            fragments = urlsplit(oldlink)
            if '.' not in fragments.path:
                log('ignoring %s' % oldlink)
                return oldlink
            base, ext = fragments.path.rsplit('.', 1)
            ext = ext.lower()
            if (not fragments.scheme.startswith('http') or
                fragments.netloc != self.server or
                ext not in ('png', 'gif', 'jpg', 'jpeg', 'svg', 'css', 'js') or
                '/pub/' not in base
                ):
                log('ignoring %s' % oldlink)
                return oldlink

            newlink = self.image_cache.fetch_if_necessary(oldlink, use_cache=self.use_cache)
            if newlink is not None:
                log('got %s as %s' % (oldlink, newlink))
                images.append(newlink)
                return newlink
            return oldlink

        self.tree.rewrite_links(localise, base_href=('http://%s/bin/view/%s/%s' %
                                                     (self.server, self.book, self.name)))
        return images

    cleaner = lxml.html.clean.Cleaner(scripts=True,
                                      javascript=True,
                                      comments=False,
                                      style=True,
                                      links=True,
                                      meta=True,
                                      page_structure=False,
                                      processing_instructions=True,
                                      embedded=True,
                                      frames=True,
                                      forms=True,
                                      annoying_tags=True,
                                      allow_tags=OK_TAGS,
                                      remove_unknown_tags=False,
                                      safe_attrs_only=True,
                                      add_nofollow=False
                                      )

    def remove_bad_tags(self):
        for e in self.tree.iter():
            if not e.tag in OK_TAGS:
                log('found bad tag %s' % e.tag)
        self.cleaner(self.tree)


class ImportedChapter(BaseChapter):
    """Used for git import"""
    def __init__(self, lang, book, chapter_name, text, author, email, date, server=None,
                 use_cache=False, cache_dir=None):
        self.lang = lang
        self.book = book
        self.name = chapter_name
        self.author = Author(author, email)
        self.date = date
        if server is None:
            server = '%s.flossmanuals.net' % lang
        self.server = server
        self.load_tree(text)
        self.use_cache = use_cache
        if cache_dir:
            self.image_cache = ImageCache(cache_dir)


class EpubChapter(BaseChapter):
    def __init__(self, server, book, chapter_name, url=None, use_cache=False,
                 cache_dir=None):
        self.server = server
        self.book = book
        self.name = chapter_name
        self.url = url
        self.use_cache = use_cache
        if cache_dir:
            self.image_cache = ImageCache(cache_dir)

    def fetch(self):
        """Fetch content as found at self.url"""
        f = urlopen(self.url)
        text = f.read()
        f.close()
        self.load_tree(text)



class BookiZip(object):

    def __init__(self, filename):
        """Start a new zip and put an uncompressed 'mimetype' file at the
        start.  This idea is copied from the epub specification, and
        allows the file type to be dscovered by reading the first few
        bytes."""
        self.zipfile = ZipFile(filename, 'w', ZIP_DEFLATED, allowZip64=True)
        self.write_blob('mimetype', MEDIATYPES['booki'], ZIP_STORED)
        self.filename = filename
        self.manifest = {}

    def write_blob(self, filename, blob, compression=ZIP_DEFLATED, mode=0644):
        """Add something to the zip without adding to manifest"""
        zinfo = ZipInfo(filename)
        zinfo.external_attr = mode << 16L # set permissions
        zinfo.compress_type = compression
        self.zipfile.writestr(zinfo, blob)

    def add_to_package(self, ID, fn, blob, mediatype=None):
        """Add an item to the zip, and save it in the manifest.  If
        mediatype is not provided, it will be guessed according to the
        extrension."""
        self.write_blob(fn, blob)
        if mediatype is None:
            ext = fn[fn.rfind('.') + 1:]
            mediatype = MEDIATYPES.get(ext, MEDIATYPES[None])
        self.manifest[ID] = (fn, mediatype)

    def _close(self):
        self.zipfile.close()

    def finish(self):
        """Finalise the metadata and write to disk"""
        self.info['manifest'] = self.manifest
        infojson = json.dumps(self.info, indent=2)
        self.add_to_package('info.json', 'info.json', infojson, 'application/json')
        self._close()

