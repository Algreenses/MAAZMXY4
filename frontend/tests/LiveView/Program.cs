using MFAAvalonia.Services;

static void Require(bool value, string message)
{
    if (!value) throw new InvalidOperationException(message);
}

var gate = new LiveViewFrameGate();
var oldFrame = gate.Snapshot();
Require(gate.CanDisplay(oldFrame, true, false), "Initial connected frame should display");
gate.Invalidate();
Require(!gate.CanDisplay(oldFrame, true, false), "A queued frame from the previous target must be discarded");
var newFrame = gate.Snapshot();
Require(!gate.CanDisplay(newFrame, false, false), "Disconnected frames must not display");
Require(!gate.CanDisplay(newFrame, true, true), "Connecting frames must not display");
Require(gate.CanDisplay(newFrame, true, false), "The new target can display once connected");
Require(gate.CanClear(newFrame, oldFrame, false), "An old frame should be cleared after changing target");
Require(!gate.CanClear(oldFrame, oldFrame, false), "An obsolete clear must not affect a later target");
Require(!gate.CanClear(newFrame, newFrame, false), "A delayed clear must not erase a newly displayed frame");
Require(!gate.CanClear(newFrame, oldFrame, true), "A disposed view must not process a queued clear");
Require(!LiveViewFrameGate.ShouldPoll(false, false, false), "Collapsed task preview must not capture");
Require(LiveViewFrameGate.ShouldPoll(false, true, false), "The monitor page keeps its independent override");
Require(!LiveViewFrameGate.ShouldPoll(true, true, true), "No preview should capture during connection");
Console.WriteLine("PASS live-view target generations, connection and monitor override");
