"""PCSC Smartcard request.

__author__ = "http://www.gemalto.com"

Copyright 2001-2007 gemalto
Author: Jean-Daniel Aussel, mailto:jean-daniel.aussel@gemalto.com

This file is part of pyscard.

pyscard is free software; you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation; either version 2.1 of the License, or
(at your option) any later version.

pyscard is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with pyscard; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
"""

import threading, time

from smartcard.AbstractCardRequest import AbstractCardRequest
from smartcard.Exceptions import CardRequestTimeoutException, CardRequestException
from smartcard.pcsc.PCSCReader import PCSCReader
from smartcard.pcsc.PCSCContext import PCSCContext
from smartcard import Card

from smartcard.scard import *

def signalEvent( evt, isInfinite ):
    if not isInfinite:
        evt.set()

class PCSCCardRequest(AbstractCardRequest):
    """PCSC CardRequest class."""

    def __init__( self, newcardonly=False, readers=None, cardType=None, cardServiceClass=None, timeout=1 ):
        """Construct new PCSCCardRequest.

        newcardonly:        if True, request a new card
                            default is False, i.e. accepts cards already inserted

        readers:            the list of readers to consider for requesting a card
                            default is to consider all readers

        cardTypeClass:      the CardType class to wait for; default is AnyCardType, i.e.
                            the request will returns with new or already inserted cards

        cardServiceClass:   the specific card service class to create and bind to the card
                            default is to create and bind a PassThruCardService

        timeout:            the time in seconds we are ready to wait for connecting to the
                            requested card.
                            default is to wait one second
                            to wait forever, set timeout to None
        """
        AbstractCardRequest.__init__( self, newcardonly, readers, cardType, cardServiceClass, timeout )

        # polling interval in ms for SCardGetStatusChange
        self.pollinginterval=300

        # if timeout is None, translate to scard.INFINITE
        if None==self.timeout:
            self.timeout=INFINITE
        # otherwise, from seconds to milliseconds
        else:
            self.timeout=int( self.timeout )

        self.hcontext = PCSCContext().getContext()

    def getReaderNames( self ):
        """Returns the list or PCSC readers on which to wait for cards."""
        # if no readers asked, use all readers
        readers=[]
        if None!=self.readersAsked:
            readers=self.readersAsked
        else:
            hresult, readers = SCardListReaders( self.hcontext, [] )
            if 0!=hresult and SCARD_E_NO_READERS_AVAILABLE!=hresult:
                raise ListReadersException( hresult )
        return readers


    def waitforcard( self ):
        """Wait for card insertion and returns a card service."""
        AbstractCardRequest.waitforcard( self )
        cardfound=False


        # for non infinite timeout, a timer will signal the end of the time-out by setting the evt event
        evt = threading.Event()
        if INFINITE==self.timeout: timertimeout=1
        else: timertimeout=self.timeout
        timer = threading.Timer( timertimeout, signalEvent, [evt, INFINITE==self.timeout] )

        # create a dictionary entry for new readers
        readerstates = {}
        readernames = self.getReaderNames()
        for reader in readernames:
            if not readerstates.has_key( reader ):
                readerstates[ reader ] = ( reader, SCARD_STATE_UNAWARE )

        # remove dictionary entry for readers that disappeared
        for oldreader in readerstates.keys():
            if oldreader not in readernames:
                del readerstates[oldreader]

        # call SCardGetStatusChange only if we have some readers
        if {}!=readerstates:
            hresult, newstates = SCardGetStatusChange( self.hcontext, 0, readerstates.values() )
        else:
            hresult=0
            newstates=[]
        if 0!=hresult and SCARD_E_TIMEOUT!=hresult:
                raise CardRequestException( 'Failed to SCardGetStatusChange ' + SCardGetErrorMessage(hresult) )


        # update readerstate
        for state in newstates:
            readername, eventstate, atr = state
            readerstates[readername] = ( readername, eventstate )

        # if a new card is not requested, just return the first available
        if not self.newcardonly:
            for state in newstates:
                readername, eventstate, atr = state
                if eventstate & SCARD_STATE_PRESENT:
                    reader=PCSCReader(readername)
                    if self.cardType.matches( atr, reader ):
                        if self.cardServiceClass.supports( 'dummy' ):
                            cardfound=True
                            return self.cardServiceClass( reader.createConnection() )


        # start timer
        timer.start()

        while not evt.isSet() and not cardfound:

            # create a dictionary entry for new readers
            readernames = self.getReaderNames()
            for reader in readernames:
                if not readerstates.has_key( reader ):
                    readerstates[ reader ] = ( reader, SCARD_STATE_UNAWARE )

            # remove dictionary entry for readers that disappeared
            for oldreader in readerstates.keys():
                if oldreader not in readernames:
                    del readerstates[oldreader]

            # wait for card insertion for self.pollinginterval
            if {}!=readerstates:
                hresult, newstates = SCardGetStatusChange( self.hcontext, self.pollinginterval, readerstates.values() )
            else:
                hresult = SCARD_E_TIMEOUT
                newstates=[]
                time.sleep(0.1)

            # real time-out, e.g. the timer has set the time-out event
            if SCARD_E_TIMEOUT==hresult and evt.isSet():
                timedout=True
                raise CardRequestTimeoutException()

            # this is a polling time-out of self.pollinginterval, make a new iteration
            elif SCARD_E_TIMEOUT==hresult:
                timedout=True

            # some error happened
            elif 0!=hresult:
                timer.cancel()
                raise CardRequestException( 'Failed to get status change ' + SCardGetErrorMessage(hresult) )

            # something changed!
            else:
                # update state dictionary
                for state in newstates:
                    readerstates[readername] = ( readername, eventstate )

                # check if we have to return a match
                for state in newstates:
                    readername, eventstate, atr = state
                    if (self.newcardonly and eventstate & SCARD_STATE_PRESENT and eventstate & SCARD_STATE_CHANGED) or (not self.newcardonly and eventstate & SCARD_STATE_PRESENT):
                        reader=PCSCReader(readername)
                        if self.cardType.matches( atr, reader ):
                            if self.cardServiceClass.supports( 'dummy' ):
                                cardfound=True
                                timer.cancel()
                                return self.cardServiceClass( reader.createConnection() )


    def waitforcardevent( self ):
        """Wait for card insertion or removal."""
        AbstractCardRequest.waitforcardevent( self )
        presentcards = []
        evt = threading.Event()

        # for non infinite timeout, a timer will signal the end of the time-out
        if INFINITE==self.timeout: timertimeout=1
        else: timertimeout=self.timeout
        timer = threading.Timer( timertimeout, signalEvent, [evt, INFINITE==self.timeout] )

        # get status change until time-out, e.g. evt is set
        readerstates = {}
        timerstarted=False

        while not evt.isSet():

            if not timerstarted:
                timerstarted=True
                timer.start()

            # reinitialize at each iteration just in case a new reader appeared
            readernames = self.getReaderNames()
            for reader in readernames:
                # create a dictionary entry for new readers
                if not readerstates.has_key( reader ):
                    readerstates[reader] = ( reader, SCARD_STATE_UNAWARE )
            # remove dictionary entry for readers that disappeared
            for oldreader in readerstates.keys():
                if oldreader not in readernames:
                    del readerstates[oldreader]

            # get status change every self.pollinginterval
            hresult, newstates = SCardGetStatusChange( self.hcontext, self.pollinginterval, readerstates.values() )

            # this is a real time-out, e.g. the event has been set
            if SCARD_E_TIMEOUT==hresult and evt.isSet():
                raise CardRequestTimeoutException()

            # this is a polling time-out of self.pollinginterval, make a new iteration
            elif SCARD_E_TIMEOUT==hresult:
                pass

            # some real error happened
            elif 0!=hresult:
                timer.cancel()
                raise CardRequestException( 'Failed to get status change ' + SCardGetErrorMessage(hresult) )

            # something changed!
            else:
                timer.cancel()
                for state in newstates:
                    readername, eventstate, atr = state
                    if eventstate & SCARD_STATE_PRESENT and eventstate & SCARD_STATE_CHANGED:
                        presentcards.append( Card.Card( readername, atr ) )
                return presentcards


if __name__ == '__main__':
    """Small sample illustrating the use of PCSCCardRequest.py."""

    from smartcard.util import toHexString
    print 'Insert a new card within 10 seconds'
    cr=PCSCCardRequest( timeout=10, newcardonly=True )
    cs = cr.waitforcard()
    cs.connection.connect()
    print cs.connection.getReader(), toHexString(cs.connection.getATR())
    cs.connection.disconnect()

