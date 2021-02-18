# -*- coding: utf-8 -*-
# from odoo import http


# class Fuse(http.Controller):
#     @http.route('/fuse/fuse/', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/fuse/fuse/objects/', auth='public')
#     def list(self, **kw):
#         return http.request.render('fuse.listing', {
#             'root': '/fuse/fuse',
#             'objects': http.request.env['fuse.fuse'].search([]),
#         })

#     @http.route('/fuse/fuse/objects/<model("fuse.fuse"):obj>/', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('fuse.object', {
#             'object': obj
#         })
