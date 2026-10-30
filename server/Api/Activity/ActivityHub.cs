using System.Collections.Concurrent;
using System.Threading.Channels;
using Marketplace.Data;

namespace Marketplace.Activity;

/// <summary>Fans new activity events out to open live feeds (server-sent events).</summary>
public class ActivityHub
{
    private readonly ConcurrentDictionary<Guid, (Guid OrgId, Channel<ActivityEvent> Channel)> subscribers = new();

    public (Guid Id, ChannelReader<ActivityEvent> Reader) Subscribe(Guid orgId)
    {
        var id = Guid.NewGuid();
        var channel = Channel.CreateUnbounded<ActivityEvent>();
        subscribers[id] = (orgId, channel);
        return (id, channel.Reader);
    }

    public void Unsubscribe(Guid id)
    {
        if (subscribers.TryRemove(id, out var sub))
        {
            sub.Channel.Writer.TryComplete();
        }
    }

    public void Publish(ActivityEvent e)
    {
        foreach (var (_, sub) in subscribers)
        {
            if (sub.OrgId == e.OrgId || sub.OrgId == e.SharedWithOrgId)
            {
                sub.Channel.Writer.TryWrite(e);
            }
        }
    }
}
