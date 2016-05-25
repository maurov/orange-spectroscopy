import numpy as np
import pyqtgraph as pg
from Orange.canvas.registry.description import Default
import Orange.data
from Orange.widgets import widget
import sys
from PyQt4 import QtGui, QtCore
from PyQt4.QtGui import QWidget, QColor, QPinchGesture
import gc
from pyqtgraph.graphicsItems.ViewBox import ViewBox
from pyqtgraph import Point
from PyQt4.QtCore import Qt, QObject, QEvent, QRectF, QPointF
from Orange.widgets.utils.plot import \
    SELECT, PANNING, ZOOMING


def closestindex(array, v):
    """
    Return index of a 1d sorted array closest to value v.
    """
    fi = np.searchsorted(array, v)
    if fi == 0:
        return 0
    elif fi == len(array):
        return len(array) - 1
    else:
        return fi-1 if v - array[fi-1] < array[fi] - v else fi

def distancetocurve(array, x, y, xpixel, ypixel, r=5, cache=None):
    if cache is not None and x in cache:
        xmin,xmax = cache[x]
    else:
        xmin = closestindex(array[0], x-r*xpixel)
        xmax = closestindex(array[0], x+r*xpixel)
        if cache is not None: 
            cache[x] = xmin,xmax
    xp = array[0][xmin:xmax+1]
    yp = array[1][xmin:xmax+1]
    
    #convert to distances in pixels
    xp = ((xp-x)/xpixel)
    yp = ((yp-y)/ypixel)
    
    distancepx = (xp**2+yp**2)**0.5
    mini = np.argmin(distancepx)
    return distancepx[mini], xmin + mini


class InteractiveViewBox(ViewBox):

    def __init__(self, graph):
        ViewBox.__init__(self, enableMenu=False)
        self.graph = graph
        self.setMouseMode(self.PanMode)
        self.zoomstartpoint = None

    def safe_update_scale_box(self, buttonDownPos, currentPos):
        x, y = currentPos
        if buttonDownPos[0] == x:
            x += 1
        if buttonDownPos[1] == y:
            y += 1
        self.updateScaleBox(buttonDownPos, Point(x, y))

    # noinspection PyPep8Naming,PyMethodOverriding
    def mouseDragEvent(self, ev, axis=None):
        if ev.button() & QtCore.Qt.RightButton:
            ev.accept()
        if self.graph.state == ZOOMING:
            ev.ignore()
            super().mouseDragEvent(ev, axis=axis)
        elif self.graph.state == PANNING:
            ev.ignore()
            super().mouseDragEvent(ev, axis=axis)
        else:
            ev.ignore()

    def suggestPadding(self, axis):
        return 0.

    def mouseMovedEvent(self, ev): #not a Qt event!
        if self.graph.state == ZOOMING and self.zoomstartpoint:
            pos = self.mapFromView(self.mapSceneToView(ev))
            self.updateScaleBox(self.zoomstartpoint, pos)

    def wheelEvent(self, ev, axis=None):
        ev.accept() #ignore wheel zoom

    def mouseClickEvent(self, ev):
        if ev.button() ==  Qt.RightButton:
            ev.accept()
            self.autoRange()
            self.graph.set_mode_panning()
        if self.graph.state != ZOOMING and ev.button() == Qt.LeftButton:
            add = True if ev.modifiers() & Qt.ControlModifier else False
            clicked_curve = self.graph.highlighted
            if clicked_curve:
                if add:
                    self.graph.selected_ids.add(clicked_curve)
                    self.graph.set_curve_pen(clicked_curve)
                else:
                    oldids = self.graph.selected_ids
                    self.graph.selected_ids = set([clicked_curve])
                    self.graph.set_curve_pens(oldids)
            else:
                oldids = self.graph.selected_ids
                self.graph.selected_ids = set()
                self.graph.set_curve_pens(oldids)
            self.graph.selection_changed()
            ev.accept()
        if self.graph.state == ZOOMING and ev.button() == Qt.LeftButton:
            if self.zoomstartpoint == None:
                self.zoomstartpoint = ev.pos()
            else:
                self.updateScaleBox(self.zoomstartpoint, ev.pos())
                self.rbScaleBox.hide()
                ax = QtCore.QRectF(Point(self.zoomstartpoint), Point(ev.pos()))
                ax = self.childGroup.mapRectFromParent(ax)
                self.showAxRect(ax)
                self.axHistoryPointer += 1
                self.axHistory = self.axHistory[:self.axHistoryPointer] + [ax]
                self.zoomstartpoint = None
            ev.accept()

    def showAxRect(self, ax):
        super().showAxRect(ax)
        if self.graph.state == ZOOMING:
            self.graph.set_mode_panning()

class SelectRegion(pg.LinearRegionItem):

    def __init__(self, *args, **kwargs):
        pg.LinearRegionItem.__init__(self, *args, **kwargs)
        for l in self.lines:
            l.setCursor(Qt.SizeHorCursor)
        self.setZValue(10)
        color = QColor(Qt.red)
        color.setAlphaF(0.05)
        self.setBrush(pg.mkBrush(color))

class CurvePlot(QWidget):

    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.plotview = pg.PlotWidget(background="w", viewBox=InteractiveViewBox(self))
        self.plot = self.plotview.getPlotItem()
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.invertX(True)
        self.curves = []
        self.curvespg = []
        self.ids = []
        self.vLine = pg.InfiniteLine(angle=90, movable=False)
        self.hLine = pg.InfiniteLine(angle=0, movable=False)
        self.proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=60, slot=self.mouseMoved, delay=0.1)
        self.plot.scene().sigMouseMoved.connect(self.plot.vb.mouseMovedEvent)
        self.plot.vb.sigRangeChanged.connect(self.resized)
        self.pen_mouse = pg.mkPen(color=(0, 0, 255), width=2)
        self.pen_normal = pg.mkPen(color=(200, 200, 200, 127), width=1)
        self.pen_subset = pg.mkPen(color=(0, 0, 0, 127), width=1)
        self.pen_selected = pg.mkPen(color=(255, 0, 0, 127), width=1)
        self.label = pg.TextItem("", anchor=(1,0))
        self.label.setText("", color=(0,0,0))

        #interface settings
        self.snap = True #snap to closest point on curve
        self.location = True #show current position
        self.markclosest = True #mark

        layout = QtGui.QGridLayout()
        self.setLayout(layout)
        self.layout().addWidget(self.plotview)
        self.highlighted = None
        self.markings = []
        self.subset_ids = set()
        self.selected_ids = set()
        self.data = None

        zoom_in = QtGui.QAction(
            "Zoom in", self, triggered=self.set_mode_zooming
        )
        zoom_in.setShortcuts([QtGui.QKeySequence(QtGui.QKeySequence.ZoomIn), Qt.Key_Z])

        zoom_fit = QtGui.QAction(
            "Fit in view", self,
            triggered=lambda x: (self.plot.vb.autoRange(), self.set_mode_panning())
        )
        zoom_fit.setShortcuts([QtGui.QKeySequence(Qt.ControlModifier | Qt.Key_0), Qt.Key_Backspace])

        self.set_mode_panning()
        self.addActions([zoom_in, zoom_fit])

    def resized(self):
        self.label.setPos(self.plot.vb.viewRect().bottomLeft())

    def selection_changed(self):
        if self.parent:
            self.parent.selection_changed()

    def mouseMoved(self, evt):
        pos = evt[0]
        if self.plot.sceneBoundingRect().contains(pos):
            mousePoint = self.plot.vb.mapSceneToView(pos)
            posx, posy = mousePoint.x(), mousePoint.y()

            if self.location:
                self.label.setText("%g, %g" % (posx, posy), color=(0,0,0))
            else:
                self.label.setText("")

            if self.curves:
                cache = {}
                R = 20
                bd = None
                if self.markclosest:
                    xpixel, ypixel = self.plot.vb.viewPixelSize()
                    distances = [ distancetocurve(c, posx, posy, xpixel, ypixel, r=R, cache=cache) for c in self.curves ]
                    bd = min(enumerate(distances), key= lambda x: x[1][0])
                if self.highlighted is not None and self.highlighted < len(self.curvespg):
                    self.set_curve_pen(self.highlighted)
                    self.highlighted = None
                if bd and bd[1][0] < R:
                    self.curvespg[bd[0]].setPen(self.pen_mouse)
                    self.curvespg[bd[0]].setZValue(5)
                    self.highlighted = bd[0]
                    posx,posy = self.curves[bd[0]][0][bd[1][1]], self.curves[bd[0]][1][bd[1][1]]

                if self.snap:
                    self.vLine.setPos(posx)
                    self.hLine.setPos(posy)

    def set_curve_pen(self, idc):
        insubset = not self.subset_ids or self.ids[idc] in self.subset_ids
        inselected = idc in self.selected_ids
        thispen = self.pen_subset if insubset else self.pen_normal
        if inselected:
            thispen = self.pen_selected
        self.curvespg[idc].setPen(thispen)
        self.curvespg[idc].setZValue(int(insubset) + int(inselected))

    def set_curve_pens(self, curves=None):
        if self.curves:
            curves = range(len(self.curves)) if curves is None else curves
            for i in curves:
                self.set_curve_pen(i)

    def clear(self):
        self.plot.vb.disableAutoRange()
        self.plotview.clear()
        self.plotview.addItem(self.label, ignoreBounds=True)
        self.data = None
        self.curves = []
        self.curvespg = []
        self.ids = []
        self.plot.addItem(self.vLine, ignoreBounds=True)
        self.plot.addItem(self.hLine, ignoreBounds=True)
        self.subset_ids = set()
        self.selected_ids = set()
        self.selection_changed()
        for m in self.markings:
            self.plot.addItem(m, ignoreBounds=True)

    def add_marking(self, item):
        self.markings.append(item)
        self.plot.addItem(item, ignoreBounds=True)

    def add_curves(self,x,ys, ids):
        """ Add multiple curves with the same x domain. """
        xsind = np.argsort(x)
        x = x[xsind]
        for y,i in zip(ys, ids):
            y = y[xsind]
            self.curves.append((x,y))
            c = pg.PlotCurveItem(x=x, y=y, pen=self.pen_normal)
            self.curvespg.append(c)
            self.ids.append(i)
            self.plot.addItem(c)

    def set_data(self, data):
        self.clear()
        if data is not None:
            self.data = data
            x = np.arange(len(data.domain.attributes))
            try:
                x = np.array([ float(a.name) for a in data.domain.attributes ])
            except:
                pass
            self.add_curves(x, data.X, data.ids)
            self.set_curve_pens()
            self.plot.vb.autoRange()

    def set_data_subset(self, ids):
        self.subset_ids = set(ids) if ids is not None else set()
        self.set_curve_pens()

    def set_mode_zooming(self):
        self.plot.vb.setMouseMode(self.plot.vb.RectMode)
        self.state = ZOOMING
        self.plot.vb.zoomstartpoint = None
        self.setCursor(Qt.CrossCursor)

    def set_mode_panning(self):
        self.plot.vb.setMouseMode(self.plot.vb.PanMode)
        self.state = PANNING
        self.plot.vb.zoomstartpoint = None
        self.unsetCursor()


class OWCurves(widget.OWWidget):
    name = "Curves"
    inputs = [("Data", Orange.data.Table, 'set_data', Default),
              ("Data subset", Orange.data.Table, 'set_subset', Default)]
    outputs = [("Selection", Orange.data.Table)]
    icon = "icons/curves.svg"

    def __init__(self):
        super().__init__()
        self.controlArea.hide()
        self.plotview = CurvePlot(self)
        self.mainArea.layout().addWidget(self.plotview)
        self.resize(900, 700)

    def set_data(self, data):
        self.plotview.set_data(data)

    def set_subset(self, data):
        self.plotview.set_data_subset(data.ids if data else None)

    def selection_changed(self):
        if self.plotview.selected_ids:
            self.send("Selection", self.plotview.data[sorted(self.plotview.selected_ids)])
        else:
            self.send("Selection", None)


def read_dpt(fn):
    """
    Temporary file reading.
    """
    tbl = np.loadtxt(fn)
    domvals = tbl.T[0] #first column is attribute name
    domain = Orange.data.Domain([Orange.data.ContinuousVariable("%f" % f) for f in domvals], None)
    datavals = tbl.T[1:]
    return Orange.data.Table(domain, datavals)


def main(argv=None):
    if argv is None:
        argv = sys.argv
    argv = list(argv)
    app = QtGui.QApplication(argv)
    w = OWCurves()
    w.show()
    data = read_dpt("/home/marko/orange-infrared/orangecontrib/infrared/datasets/2012.11.09-11.45_Peach juice colorful spot.dpt")
    data = Orange.data.Table("/home/marko/Downloads/testdata.csv")
    w.set_data(data)
    w.set_subset(data[:10])
    #w.set_subset(None)
    w.handleNewSignals()
    region = SelectRegion()
    def update():
        minX, maxX = region.getRegion()
        print(minX, maxX)
    region.sigRegionChanged.connect(update)
    w.plotview.add_marking(region)
    rval = app.exec_()
    w.set_data(None)
    w.handleNewSignals()
    w.deleteLater()
    del w
    app.processEvents()
    gc.collect()
    return rval

if __name__ == "__main__":
    sys.exit(main())

