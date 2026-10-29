using System.Threading;

namespace MFAAvalonia.Services;

/// <summary>Reject frames submitted before a target or connection changed.</summary>
public sealed class LiveViewFrameGate
{
    private long _generation;
    public long Snapshot() => Interlocked.Read(ref _generation);
    public long Invalidate() => Interlocked.Increment(ref _generation);
    public bool IsCurrent(long generation) => Snapshot() == generation;
    public bool CanDisplay(long generation, bool connected, bool connecting) =>
        IsCurrent(generation) && connected && !connecting;

    public bool CanClear(long generation, long displayedGeneration, bool disposed) =>
        !disposed && IsCurrent(generation) && displayedGeneration != generation;

    public static bool ShouldPoll(bool expanded, bool monitorOverride, bool connecting) =>
        (expanded || monitorOverride) && !connecting;
}
