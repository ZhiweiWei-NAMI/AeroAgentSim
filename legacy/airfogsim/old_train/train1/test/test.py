from torch.utils.tensorboard import SummaryWriter

writer = SummaryWriter("./logs")

last_episode=100
max_episode=200
for episode in range(last_episode + 1, max_episode + 1):
    writer.add_scalar("test", episode-50, episode)
writer.close()
