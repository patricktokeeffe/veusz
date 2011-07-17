# plotwindow.py
# the main window for showing plots
 
#    Copyright (C) 2004 Jeremy S. Sanders
#    Email: Jeremy Sanders <jeremy@jeremysanders.net>
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License along
#    with this program; if not, write to the Free Software Foundation, Inc.,
#    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
##############################################################################

import sys
from itertools import izip

import veusz.qtall as qt4
import numpy as N

import veusz.setting as setting
import veusz.dialogs.exceptiondialog as exceptiondialog
import veusz.document as document
import veusz.utils as utils
import veusz.widgets as widgets

class PickerCrosshairItem( qt4.QGraphicsPathItem ):
    """The picker cross widget: it moves from point to point and curve to curve
       with the arrow keys, and hides itself when it looses focus"""
    def __init__(self, parent=None):
        path = qt4.QPainterPath()
        path.addRect(-4, -4, 8, 8)
        path.addRect(-5, -5, 10, 10)
        path.moveTo(-8, 0)
        path.lineTo(8, 0)
        path.moveTo(0, -8)
        path.lineTo(0, 8)

        qt4.QGraphicsPathItem.__init__(self, path, parent)
        self.setBrush(qt4.QBrush(qt4.Qt.black))
        self.setFlags(self.flags() | qt4.QGraphicsItem.ItemIsFocusable)

    def paint(self, painter, option, widget):
        """Override this to enforce the global antialiasing setting"""
        aa = setting.settingdb['plot_antialias']
        painter.save()
        painter.setRenderHint(qt4.QPainter.Antialiasing, aa)
        qt4.QGraphicsPathItem.paint(self, painter, option, widget)
        painter.restore()

    def focusOutEvent(self, event):
        qt4.QGraphicsPathItem.focusOutEvent(self, event)
        self.hide()

class PlotWindow( qt4.QGraphicsView ):
    """Class to show the plot(s) in a scrollable window."""

    intervals = (0, 100, 250, 500, 1000, 2000, 5000, 10000)

    def __init__(self, document, parent, menu=None):
        """Initialise the window.

        menu gives a menu to add any menu items to
        """

        qt4.QGraphicsView.__init__(self, parent)
        self.setBackgroundRole(qt4.QPalette.Dark)
        self.scene = qt4.QGraphicsScene()
        self.setScene(self.scene)

        # this graphics scene item is the actual graph
        self.pixmapitem = self.scene.addPixmap( qt4.QPixmap(1, 1) )
        self.controlgraphs = []
        self.widgetcontrolgraphs = {}
        self.selwidget = None
        self.vzactions = None

        # zoom rectangle for zooming into graph (not shown normally)
        self.zoomrect = self.scene.addRect( 0, 0, 100, 100,
                                            qt4.QPen(qt4.Qt.DotLine) )
        self.zoomrect.setZValue(2.)
        self.zoomrect.hide()

        # picker graphicsitem for marking the picked point
        self.pickeritem = PickerCrosshairItem()
        self.scene.addItem(self.pickeritem)
        self.pickeritem.setZValue(2.)
        self.pickeritem.hide()

        # all the widgets that picker key-navigation might cycle through
        self.pickerwidgets = []

        # the picker state
        self.pickerinfo = widgets.PickInfo()

        # set up so if document is modified we are notified
        self.document = document
        self.docchangeset = -100

        # state of last plot from painthelper
        self.painthelper = None

        self.size = (1, 1)
        self.oldzoom = -1.
        self.zoomfactor = 1.
        self.pagenumber = 0
        self.forceupdate = False
        self.ignoreclick = False

        # work out dpi
        self.screendpi = self.logicalDpiY()

        # convert size to pixels
        self.setOutputSize()

        # mode for clicking
        self.clickmode = 'select'
        self.currentclickmode = None

        # list of widgets and positions last painted
        self.widgetpositions = []
        self.widgetpositionslookup = {}

        # set up redrawing timer
        self.timer = qt4.QTimer(self)
        self.connect( self.timer, qt4.SIGNAL('timeout()'),
                      self.slotTimeout )

        # for drag scrolling
        self.grabpos = None
        self.scrolltimer = qt4.QTimer(self)
        self.scrolltimer.setSingleShot(True)

        # for turning clicking into scrolling after a period
        self.connect( self.scrolltimer, qt4.SIGNAL('timeout()'),
                      self.slotBecomeScrollClick )

        # get update period from setting database
        self.interval = setting.settingdb['plot_updateinterval']

        # load antialias settings
        self.antialias = setting.settingdb['plot_antialias']

        if self.interval > 0:
            self.timer.start(self.interval)

        # allow window to get focus, to allow context menu
        self.setFocusPolicy(qt4.Qt.StrongFocus)

        # get mouse move events if mouse is not pressed
        self.setMouseTracking(True)

        # create toolbar in main window (urgh)
        self.createToolbar(parent, menu)

        # make the context menu object
        self._constructContextMenu()

    def showToolbar(self, show=True):
        """Show or hide toolbar"""
        self.viewtoolbar.setVisible(show)

    def createToolbar(self, parent, menu=None):
        """Make a view toolbar, and optionally update menu."""

        self.viewtoolbar = qt4.QToolBar("View toolbar - Veusz", parent)
        self.viewtoolbar.setObjectName('veuszviewtoolbar')
        iconsize = setting.settingdb['toolbar_size']
        self.viewtoolbar.setIconSize(qt4.QSize(iconsize, iconsize))
        self.viewtoolbar.hide()
        parent.addToolBar(qt4.Qt.TopToolBarArea, self.viewtoolbar)

        if hasattr(parent, 'vzactions'):
            # share actions with parent if possible
            # as plot windows can be isolated from mainwindows, we need this
            self.vzactions = actions = parent.vzactions
        else:
            self.vzactions = actions = {}

        a = utils.makeAction
        actions.update({
                'view.zoomin':
                    a(self, 'Zoom into the plot', 'Zoom &In',
                      self.slotViewZoomIn,
                      icon='kde-zoom-in', key='Ctrl++'),
                'view.zoomout':
                    a(self, 'Zoom out of the plot', 'Zoom &Out',
                      self.slotViewZoomOut,
                      icon='kde-zoom-out', key='Ctrl+-'),
                'view.zoom11':
                    a(self, 'Restore plot to natural size', 'Zoom 1:1',
                      self.slotViewZoom11,
                      icon='kde-zoom-1-veuszedit', key='Ctrl+1'),
                'view.zoomwidth':
                    a(self, 'Zoom plot to show whole width', 'Zoom to width',
                      self.slotViewZoomWidth,
                      icon='kde-zoom-width-veuszedit'),
                'view.zoomheight':
                    a(self, 'Zoom plot to show whole height', 'Zoom to height',
                      self.slotViewZoomHeight,
                      icon='kde-zoom-height-veuszedit'),
                'view.zoompage':
                    a(self, 'Zoom plot to show whole page', 'Zoom to page',
                      self.slotViewZoomPage,
                      icon='kde-zoom-page-veuszedit'),
                'view.zoommenu':
                    a(self, 'Zoom functions menu', 'Zoom',
                      self.doZoomMenuButton,
                      icon='kde-zoom-veuszedit'),
                'view.prevpage':
                    a(self, 'Move to the previous page', '&Previous page',
                      self.slotViewPreviousPage,
                      icon='kde-go-previous', key='Ctrl+PgUp'),
                'view.nextpage':
                    a(self, 'Move to the next page', '&Next page',
                      self.slotViewNextPage,
                      icon='kde-go-next', key='Ctrl+PgDown'),
                'view.select':
                    a(self, 'Select items from the graph or scroll',
                      'Select items or scroll',
                      None,
                      icon='kde-mouse-pointer'),
                'view.pick':
                    a(self, 'Read data points on the graph',
                      'Read data points',
                      None,
                      icon='veusz-pick-data'),
                'view.zoomgraph':
                    a(self, 'Zoom into graph', 'Zoom graph',
                      None,
                      icon='veusz-zoom-graph'),
                })

        if menu:
            # only construct menu if required
            menuitems = [
                ('view', '', [
                        'view.zoomin', 'view.zoomout',
                        'view.zoom11', 'view.zoomwidth',
                        'view.zoomheight', 'view.zoompage',
                        '',
                        'view.prevpage', 'view.nextpage',
                        'view.select', 'view.pick', 'view.zoomgraph',
                        ]),
                ]
            utils.constructMenus(menu, {'view': menu}, menuitems,
                                 actions)

        # populate menu on zoom menu toolbar icon
        zoommenu = qt4.QMenu(self)
        zoomag = qt4.QActionGroup(self)
        for act in ('view.zoomin', 'view.zoomout', 'view.zoom11',
                    'view.zoomwidth', 'view.zoomheight', 'view.zoompage'):
            a = actions[act]
            zoommenu.addAction(a)
            zoomag.addAction(a)
            a.vzname = act
        actions['view.zoommenu'].setMenu(zoommenu)
        self.connect(zoomag, qt4.SIGNAL('triggered(QAction*)'),
                     self.zoomActionTriggered)

        lastzoom = setting.settingdb.get('view_defaultzoom', 'view.zoompage')
        self.updateZoomMenuButton(actions[lastzoom])

        # add items to toolbar
        utils.addToolbarActions(self.viewtoolbar, actions,
                                ('view.prevpage', 'view.nextpage',
                                 'view.select', 'view.pick',
                                 'view.zoomgraph', 'view.zoommenu'))

        # define action group for various different selection models
        grp = self.selectactiongrp = qt4.QActionGroup(self)
        grp.setExclusive(True)
        for a in ('view.select', 'view.pick', 'view.zoomgraph'):
            actions[a].setActionGroup(grp)
            actions[a].setCheckable(True)
        actions['view.select'].setChecked(True)
        self.connect( grp, qt4.SIGNAL('triggered(QAction*)'),
                      self.slotSelectMode )

        return self.viewtoolbar

    def zoomActionTriggered(self, action):
        """Keep track of the last zoom action selected."""
        setting.settingdb['view_defaultzoom'] = action.vzname
        self.updateZoomMenuButton(action)

    def updateZoomMenuButton(self, action):
        """Make zoom button call default zoom action and change icon."""
        menuact = self.vzactions['view.zoommenu']
        setting.settingdb['view_defaultzoom'] = action.vzname
        menuact.setIcon( action.icon() )

    def doZoomMenuButton(self):
        """Select previous zoom option when clicking on zoom menu."""
        act = self.vzactions[setting.settingdb['view_defaultzoom']]
        act.trigger()

    def doZoomRect(self, endpos):
        """Take the zoom rectangle drawn by the user and do the zooming.
        endpos is a QPoint end point

        This is pretty messy - first we have to work out the graph associated
        to the first point

        Then we have to iterate over each of the plotters, identify their
        axes, and change the range of the axes to match the screen region
        selected.
        """

        # safety net
        if self.grabpos is None or endpos is None:
            return

        # get points corresponding to corners of rectangle
        pt1 = self.grabpos
        pt2 = endpos

        # work out whether it's worthwhile to zoom: only zoom if there
        # are >=5 pixels movement
        if abs((pt2-pt1).x()) < 10 or abs((pt2-pt1).y()) < 10:
            return

        # try to work out in which widget the first point is in
        widget = self.painthelper.pointInWidgetBounds(
            self.document.basewidget, pt1.x(), pt1.y(), widgets.Graph)
        if widget is None:
            return
        
        # convert points on plotter to points on axis for each axis
        xpts = N.array( [pt1.x(), pt2.x()] )
        ypts = N.array( [pt1.y(), pt2.y()] )

        # build up operation list to do zoom
        operations = []
        
        axes = {}
        # iterate over children, to look for plotters
        for c in [i for i in widget.children if
                  isinstance(i, widgets.GenericPlotter)]:

            # get axes associated with plotter
            caxes = c.parent.getAxes( (c.settings.xAxis,
                                      c.settings.yAxis) )

            for a in caxes:
                if a:
                    axes[a] = True

        # iterate over each axis, and update the ranges
        for axis in axes.iterkeys():
            s = axis.settings
            if s.direction == 'horizontal':
                p = xpts
            else:
                p = ypts

            # convert points on plotter to axis coordinates
            # FIXME: Need To Trap Conversion Errors!
            r = axis.plotterToGraphCoords(
                self.painthelper.states[axis].bounds, p)

            # invert if min and max are inverted
            if r[1] < r[0]:
                r[1], r[0] = r[0], r[1]

            # build up operations to change axis
            if s.min != r[0]:
                operations.append( document.OperationSettingSet(s.get('min'),
                                                                float(r[0])) )
            if s.max != r[1]:
                operations.append( document.OperationSettingSet(s.get('max'),
                                                                float(r[1])) )

        # finally change the axes
        self.document.applyOperation(
            document.OperationMultiple(operations,descr='zoom axes') )

    def axesForPoint(self, mousepos):
        """Find all the axes which contain the given mouse position"""
        pos = self.mapToScene(mousepos)
        px, py = pos.x(), pos.y()

        axes = []
        for widget, bounds in self.widgetpositions:
            # if widget is axis, and point lies within bounds
            if ( isinstance(widget, widgets.Axis) and
                 px>=bounds[0] and px<=bounds[2] and
                 py>=bounds[1] and py<=bounds[3] ):

                # convert correct pointer position
                if widget.settings.direction == 'horizontal':
                    val = px
                else:
                    val = py
                coords=widget.plotterToGraphCoords(bounds, N.array([val]))
                axes.append( (widget, coords[0]) )

        return axes

    def emitPicked(self, pickinfo):
        """Report that a new point has been picked"""

        self.pickerinfo = pickinfo
        self.pickeritem.setPos(pickinfo.screenpos[0], pickinfo.screenpos[1])
        self.emit(qt4.SIGNAL("sigPointPicked"), pickinfo)

    def doPick(self, mousepos):
        """Find the point on any plot-like widget closest to the cursor"""

        self.pickerwidgets = []

        pickinfo = widgets.PickInfo()
        pos = self.mapToScene(mousepos)

        for w, bounds in self.widgetpositions:
            try:
                # ask the widget for its (visually) closest point to the cursor
                info = w.pickPoint(pos.x(), pos.y(), bounds)

                # this is a pickable widget, so remember it for future key navigation
                self.pickerwidgets.append(w)

                if info.distance < pickinfo.distance:
                    # and remember the overall closest
                    pickinfo = info
            except AttributeError:
                # ignore widgets that don't support axes or picking
                continue

        if not pickinfo:
            self.pickeritem.hide()
            return

        self.emitPicked(pickinfo)

    def slotBecomeScrollClick(self):
        """If the click is still down when this timer is reached then
        we turn the click into a scrolling click."""

        if self.currentclickmode == 'select':
            qt4.QApplication.setOverrideCursor(qt4.QCursor(qt4.Qt.SizeAllCursor))
            self.currentclickmode = 'scroll'

    def mousePressEvent(self, event):
        """Allow user to drag window around."""

        qt4.QGraphicsView.mousePressEvent(self, event)

        # work out whether user is clicking on a control point
        self.ignoreclick = self.itemAt(event.pos()) is not self.pixmapitem

        if event.button() == qt4.Qt.LeftButton and not self.ignoreclick:

            # need to copy position, otherwise it gets reused!
            self.winpos = qt4.QPoint(event.pos())
            self.grabpos = self.mapToScene(self.winpos)

            if self.clickmode == 'select':
                # we set this to true unless the timer runs out (400ms),
                # then it becomes a scroll click
                # scroll clicks drag the window around, and selecting clicks
                # select widgets!
                self.scrolltimer.start(400)

            elif self.clickmode == 'pick':
                self.pickeritem.show()
                self.pickeritem.setFocus(qt4.Qt.MouseFocusReason)
                self.doPick(event.pos())

            elif self.clickmode == 'scroll':
                qt4.QApplication.setOverrideCursor(
                    qt4.QCursor(qt4.Qt.SizeAllCursor))

            elif self.clickmode == 'graphzoom':
                self.zoomrect.setRect(self.grabpos.x(), self.grabpos.y(),
                                      0, 0)
                self.zoomrect.show()

                #self.label.drawRect(self.grabpos, self.grabpos)

            # record what mode we were clicked in
            self.currentclickmode = self.clickmode

    def mouseMoveEvent(self, event):
        """Scroll window by how much the mouse has moved since last time."""

        qt4.QGraphicsView.mouseMoveEvent(self, event)

        if self.currentclickmode == 'scroll':
            event.accept()

            # move scroll bars by amount
            pos = event.pos()
            dx = self.winpos.x()-pos.x()
            scrollx = self.horizontalScrollBar()
            scrollx.setValue( scrollx.value() + dx )

            dy = self.winpos.y()-pos.y()
            scrolly = self.verticalScrollBar()
            scrolly.setValue( scrolly.value() + dy )

            # need to copy point
            self.winpos = qt4.QPoint(event.pos())

        elif self.currentclickmode == 'graphzoom' and self.grabpos is not None:
            pos = self.mapToScene(event.pos())
            r = self.zoomrect.rect()
            self.zoomrect.setRect( r.x(), r.y(), pos.x()-r.x(),
                                   pos.y()-r.y() )

        elif self.clickmode == 'select' or self.clickmode == 'pick':
            # find axes which map to this position
            axes = self.axesForPoint(event.pos())
            vals = dict([ (a[0].name, a[1]) for a in axes ])

            self.emit( qt4.SIGNAL('sigAxisValuesFromMouse'), vals )

            if self.currentclickmode == 'pick':
                # drag the picker around
                self.doPick(event.pos())

    def mouseReleaseEvent(self, event):
        """If the mouse button is released, check whether the mouse
        clicked on a widget, and emit a sigWidgetClicked(widget)."""

        qt4.QGraphicsView.mouseReleaseEvent(self, event)
        if event.button() == qt4.Qt.LeftButton and not self.ignoreclick:
            event.accept()
            self.scrolltimer.stop()
            if self.currentclickmode == 'select':
                # work out where the mouse clicked and choose widget
                pos = self.mapToScene(event.pos())
                self.locateClickWidget(pos.x(), pos.y())
            elif self.currentclickmode == 'scroll':
                # return the cursor to normal after scrolling
                self.clickmode = 'select'
                self.currentclickmode = None
                qt4.QApplication.restoreOverrideCursor()
            elif self.currentclickmode == 'graphzoom':
                self.zoomrect.hide()
                self.doZoomRect(self.mapToScene(event.pos()))
                self.grabpos = None
            elif self.currentclickmode == 'viewgetclick':
                self.clickmode = 'select'
            elif self.currentclickmode == 'pick':
                self.currentclickmode = None

    def keyPressEvent(self, event):
        """Keypad motion moves the picker if it has focus"""
        if self.pickeritem.hasFocus():
            event.accept()
            k = event.key()
            if k == qt4.Qt.Key_Left or k == qt4.Qt.Key_Right:
                # navigate to the previous or next point on the curve
                dir = 'right' if k == qt4.Qt.Key_Right else 'left'
                ix = self.pickerinfo.index
                pickinfo = self.pickerinfo.widget.pickIndex(ix, dir,
                                self.widgetpositionslookup[self.pickerinfo.widget])
                if not pickinfo:
                    # no more points visible in this direction
                    return

                self.emitPicked(pickinfo)
            elif k == qt4.Qt.Key_Up or k == qt4.Qt.Key_Down:
                # navigate to the next plot up or down on the screen
                p = self.pickeritem.pos()

                oldw = self.pickerinfo.widget
                pickinfo = widgets.PickInfo()

                dist = float('inf')
                for w in self.pickerwidgets:
                    if w == oldw:
                        continue

                    # ask the widgets to pick their point which is closest horizontally
                    # to the last (screen) x value picked
                    pi = w.pickPoint(self.pickerinfo.screenpos[0], p.y(),
                                     self.widgetpositionslookup[w],
                                     distance='horizontal')
                    if not pi:
                        continue

                    dy = p.y() - pi.screenpos[1]

                    # take the new point which is closest vertically to the current
                    # one and either above or below it as appropriate
                    if abs(dy) < dist and ( (k == qt4.Qt.Key_Up and dy > 0)
                            or (k == qt4.Qt.Key_Down and dy < 0) ):
                        pickinfo = pi
                        dist = abs(dy)

                if pickinfo:
                    oldx = self.pickerinfo.screenpos[0]
                    self.emitPicked(pickinfo)

                    # restore the previous x-position, so that vertical navigation
                    # stays repeatable
                    pickinfo.screenpos = (oldx, pickinfo.screenpos[1])

    def locateClickWidget(self, x, y):
        """Work out which widget was clicked, and if necessary send
        a sigWidgetClicked(widget) signal."""
        
        if self.document.getNumberPages() == 0:
            return

        widget = self.painthelper.identifyWidgetAtPoint(
            self.document.basewidget, x, y, antialias=self.antialias)
        if widget is None:
            # select page if nothing clicked
            widget = self.document.basewidget.getPage(self.pagenumber)

        # tell connected objects that widget was clicked
        if widget is not None:
            self.emit( qt4.SIGNAL('sigWidgetClicked'), widget )

    def setOutputSize(self):
        """Set the ouput display size."""

        # when window opens there are no pages
        if self.pagenumber >= self.document.getNumberPages():
            return

        size = self.document.pageSize(self.pagenumber, scaling=self.zoomfactor)

        # if size has changed, make new buffer and resize widget
        if size != self.size:
            self.size = size
            self.bufferpixmap = qt4.QPixmap( *self.size )
            self.forceupdate = True
            self.setSceneRect( 0, 0, size[0], size[1] )

    def setPageNumber(self, pageno):
        """Move the the selected page."""

        # we don't need to do anything
        if (self.pagenumber == pageno and
            self.document.changeset == self.docchangeset):
            return

        # keep within bounds
        pageno = min(pageno, self.document.getNumberPages()-1)
        pageno = max(0, pageno)

        self.pagenumber = pageno
        self.forceupdate = True

    def getPageNumber(self):
        """Get the the selected page."""
        return self.pagenumber

    def slotTimeout(self):
        """Called after timer times out, to check for updates to window."""

        # no threads, so can't get interrupted here
        # draw data into background pixmap if modified
        if ( self.zoomfactor != self.oldzoom or
             self.document.changeset != self.docchangeset or
             self.forceupdate ):

            self.pickeritem.hide()
            self.setOutputSize()
            
            # fill pixmap with proper background colour
            self.bufferpixmap.fill( setting.settingdb.color('page') )

            self.pagenumber = min( self.document.getNumberPages() - 1,
                                   self.pagenumber )
            if self.pagenumber >= 0:
                # draw the data into the buffer
                # errors cause an exception window to pop up
                try:
                    self.painthelper = phelper = document.PaintHelper(
                        (self.bufferpixmap.width(),
                         self.bufferpixmap.height()),
                        scaling = self.zoomfactor,
                        dpi=(self.bufferpixmap.logicalDpiX(),
                             self.bufferpixmap.logicalDpiY())
                        )
                    self.document.paintTo(phelper, self.pagenumber)

                    painter = qt4.QPainter(self.bufferpixmap)
                    painter.setRenderHint(qt4.QPainter.Antialiasing,
                                          self.antialias)
                    painter.setRenderHint(qt4.QPainter.TextAntialiasing,
                                          self.antialias)
                    phelper.renderToPainter(self.document.basewidget,
                                            painter)
                    painter.end()

                    # update selected widget items
                    self.selectedWidgets([self.selwidget])
                    
                except Exception:
                    # stop updates this time round and show exception dialog
                    d = exceptiondialog.ExceptionDialog(sys.exc_info(), self)
                    self.oldzoom = self.zoomfactor
                    self.forceupdate = False
                    self.docchangeset = self.document.changeset
                    d.exec_()
                    
            else:
                self.pagenumber = 0

            self.emit( qt4.SIGNAL("sigUpdatePage"), self.pagenumber )
            self.updatePageToolbar()

            self.oldzoom = self.zoomfactor
            self.forceupdate = False
            self.docchangeset = self.document.changeset

            self.pixmapitem.setPixmap(self.bufferpixmap)

    def _constructContextMenu(self):
        """Construct the context menu."""

        menu = self.contextmenu = qt4.QMenu(self)

        # add some useful entries
        menu.addAction( self.vzactions['view.zoommenu'] )
        menu.addSeparator()
        menu.addAction( self.vzactions['view.prevpage'] )
        menu.addAction( self.vzactions['view.nextpage'] )
        menu.addSeparator()

        # update NOW!
        menu.addAction('Force update', self.actionForceUpdate)

        # Update submenu
        submenu = menu.addMenu('Updates')
        intgrp = qt4.QActionGroup(self)

        inttext = ['Disable']
        for intv in self.intervals[1:]:
            inttext.append('Every %gs' % (intv * 0.001))

        # need to keep copies of bound objects otherwise they are collected
        self._intfuncs = []

        # bind interval options to actions
        for intv, text in izip(self.intervals, inttext):
            act = intgrp.addAction(text)
            act.setCheckable(True)
            fn = utils.BoundCaller(self.actionSetTimeout, intv)
            self._intfuncs.append(fn)
            self.connect(act, qt4.SIGNAL('triggered(bool)'), fn)
            if intv == self.interval:
                act.setChecked(True)
            submenu.addAction(act)

        # antialias
        menu.addSeparator()
        act = menu.addAction('Antialias', self.actionAntialias)
        act.setCheckable(True)
        act.setChecked(self.antialias)

    def updatePlotSettings(self):
        """Update plot window settings from settings."""
        self.setTimeout(setting.settingdb['plot_updateinterval'])
        self.antialias = setting.settingdb['plot_antialias']
        self.actionForceUpdate()

    def contextMenuEvent(self, event):
        """Show context menu."""
        self.contextmenu.exec_(qt4.QCursor.pos())

    def actionForceUpdate(self):
        """Force an update for the graph."""
        self.docchangeset = -100
        self.slotTimeout()

    def setTimeout(self, interval):
        """Change timer setting without changing save value."""
        if interval == 0:
            if self.timer.isActive():
                self.timer.stop()
        else:
            self.timer.setInterval(interval)
            if not self.timer.isActive():
                self.timer.start()

    def actionSetTimeout(self, interval, checked):
        """Called by setting the interval."""

        if interval == 0:
            # stop updates
            self.interval = 0
            if self.timer.isActive():
                self.timer.stop()
        else:
            # change interval to one selected
            self.interval = interval
            self.timer.setInterval(interval)
            # start timer if it was stopped
            if not self.timer.isActive():
                self.timer.start()

        # remember changes for next time
        setting.settingdb['plot_updateinterval'] = self.interval

    def actionAntialias(self):
        """Toggle antialias."""
        self.antialias = not self.antialias
        setting.settingdb['plot_antialias'] = self.antialias
        self.actionForceUpdate()

    def setZoomFactor(self, zoomfactor):
        """Set the zoom factor of the window."""
        self.zoomfactor = float(zoomfactor)
        self.update()

    def slotViewZoomIn(self):
        """Zoom into the plot."""
        self.setZoomFactor(self.zoomfactor * N.sqrt(2.))

    def slotViewZoomOut(self):
        """Zoom out of the plot."""
        self.setZoomFactor(self.zoomfactor / N.sqrt(2.))

    def slotViewZoomWidth(self):
        """Make the zoom factor so that the plot fills the whole width."""

        # need to take account of scroll bars when deciding size
        viewportsize = self.maximumViewportSize()
        aspectwin = viewportsize.width()*1./viewportsize.height()
        aspectplot = self.size[0]*1./self.size[1]

        width = viewportsize.width()
        if aspectwin > aspectplot:
            # take account of scroll bar
            width -= self.verticalScrollBar().width()
            
        mult = width*1./self.size[0]
        self.setZoomFactor(self.zoomfactor * mult)
        
    def slotViewZoomHeight(self):
        """Make the zoom factor so that the plot fills the whole width."""

        # need to take account of scroll bars when deciding size
        viewportsize = self.maximumViewportSize()
        aspectwin = viewportsize.width()*1./viewportsize.height()
        aspectplot = self.size[0]*1./self.size[1]

        height = viewportsize.height()
        if aspectwin < aspectplot:
            # take account of scroll bar
            height -= self.horizontalScrollBar().height()
            
        mult = height*1./self.size[1]
        self.setZoomFactor(self.zoomfactor * mult)

    def slotViewZoomPage(self):
        """Make the zoom factor correct to show the whole page."""

        viewportsize = self.maximumViewportSize()
        multw = viewportsize.width()*1./self.size[0]
        multh = viewportsize.height()*1./self.size[1]
        self.setZoomFactor(self.zoomfactor * min(multw, multh))

    def slotViewZoom11(self):
        """Restore the zoom to 1:1"""
        self.setZoomFactor(1.)

    def slotViewPreviousPage(self):
        """View the previous page."""
        self.setPageNumber( self.pagenumber - 1 )
 
    def slotViewNextPage(self):
        """View the next page."""
        self.setPageNumber( self.pagenumber + 1 )

    def updatePageToolbar(self):
        """Update page number when the plot window says so."""

        # disable previous and next page actions
        if self.vzactions is not None:
            np = self.document.getNumberPages()
            self.vzactions['view.prevpage'].setEnabled(self.pagenumber != 0)
            self.vzactions['view.nextpage'].setEnabled(self.pagenumber < np-1)

    def slotSelectMode(self, action):
        """Called when the selection mode has changed."""

        modecnvt = { self.vzactions['view.select'] : 'select',
                     self.vzactions['view.pick'] : 'pick',
                     self.vzactions['view.zoomgraph'] : 'graphzoom' }
        
        # close the current picker
        self.pickeritem.hide()
        self.emit(qt4.SIGNAL('sigPickerEnabled'), False)

        # convert action into clicking mode
        self.clickmode = modecnvt[action]

        if self.clickmode == 'select':
            self.pixmapitem.unsetCursor()
            #self.label.setCursor(qt4.Qt.ArrowCursor)
        elif self.clickmode == 'graphzoom':
            self.pixmapitem.unsetCursor()
            #self.label.setCursor(qt4.Qt.CrossCursor)
        elif self.clickmode == 'pick':
            self.pixmapitem.setCursor(qt4.Qt.CrossCursor)
            self.emit(qt4.SIGNAL('sigPickerEnabled'), True)
        
    def getClick(self):
        """Return a click point from the graph."""

        # wait for click from user
        qt4.QApplication.setOverrideCursor(qt4.QCursor(qt4.Qt.CrossCursor))
        oldmode = self.clickmode
        self.clickmode = 'viewgetclick'
        while self.clickmode == 'viewgetclick':
            qt4.qApp.processEvents()
        self.clickmode = oldmode
        qt4.QApplication.restoreOverrideCursor()

        # take clicked point and convert to coords of scrollview
        pt = self.grabpos

        # try to work out in which widget the first point is in
        bufferpixmap = qt4.QPixmap( *self.size )
        painter = PointPainter(bufferpixmap, pt.x(), pt.y())
        pagenumber = min( self.document.getNumberPages() - 1,
                          self.pagenumber )
        if pagenumber >= 0:
            self.document.paintTo(painter, self.pagenumber,
                                  scaling=self.zoomfactor, dpi=self.widgetdpi)
        painter.end()

        # get widget
        widget = painter.widget
        if widget is None:
            return []
        
        # convert points on plotter to points on axis for each axis
        xpts = N.array( [pt.x()] )
        ypts = N.array( [pt.y()] )

        axesretn = []
        # iterate over children, to look for plotters
        for c in [i for i in widget.children if
                  isinstance(i, widgets.GenericPlotter)]:

            # get axes associated with plotter
            axes = c.parent.getAxes( (c.settings.xAxis,
                                      c.settings.yAxis) )

            # iterate over each, and update the ranges
            for axis in [a for a in axes if a is not None]:
                s = axis.settings
                if s.direction == 'horizontal':
                    p = xpts
                else:
                    p = ypts

                # convert point on plotter to axis coordinate
                # FIXME: Need To Trap Conversion Errors!
                r = axis.plotterToGraphCoords(painter.bounds[axis], p)

                axesretn.append( (axis.path, r[0]) )

        return axesretn

    def selectedWidgets(self, widgets):
        """Update control items on screen associated with widget."""

        if not widgets:
            self.selwidget = None
        else:
            self.selwidget = widgets[0]

        # remove old items from scene
        for item in self.controlgraphs:
            self.scene.removeItem(item)
        del self.controlgraphs[:]

        # put in new items
        for widget in widgets:
            if self.painthelper and widget in self.painthelper.states:
                for control in self.painthelper.states[widget].cgis:
                    graphitem = control.createGraphicsItem()
                    self.controlgraphs.append(graphitem)
                    self.scene.addItem(graphitem)
